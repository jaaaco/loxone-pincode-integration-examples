#!/usr/bin/env node
/**
 * Command-line helper for managing Loxone user PIN codes via CloudDNS.
 *
 * Two integration models:
 *   Model A (static user per door):  set-pin / clear-pin
 *   Model B (ephemeral user per reservation, in a group):
 *     list-groups / create-guest / delete-user
 */

const SERIAL = process.env.SERIAL || "YOUR-MINISERVER-SERIAL"; // 12 hex chars, MAC without colons
const AUTH = process.env.AUTH || "USERNAME:PASSWORD";

// CloudDNS only resolves a serial for the Loxone app's own User-Agent. Since ~11.2026 any other
// UA (curl, a browser, a bare integration) gets 403 "Request not permitted", and the old
// reverse-proxy path dns.loxonecloud.com/<serial>/... is gone for good. getip still answers, and
// the host it returns is a direct TLS endpoint on the Miniserver's public IP that needs no special
// UA. So: resolve once with the app's UA, then talk straight to the Miniserver.
const CLOUD_RESOLVER = process.env.CLOUD_RESOLVER || "https://connect.loxonecloud.com";
const RESOLVER_UA = process.env.RESOLVER_UA || "ktor-client";

// Loxone timestamps count seconds from 2009-01-01 00:00:00 UTC, not the Unix epoch.
const LOXONE_EPOCH_OFFSET = 1230768000;

if (!SERIAL || !AUTH) {
  console.error("SERIAL and AUTH must be defined");
  process.exit(1);
}

function toLoxoneEpoch(unixSeconds) {
  return Math.floor(Number(unixSeconds)) - LOXONE_EPOCH_OFFSET;
}

function basicAuthHeader(auth) {
  return "Basic " + Buffer.from(auth, "utf8").toString("base64");
}

function httpRequest(url) {
  return new Promise((resolve, reject) => {
    const { URL } = require("url");
    const target = new URL(url);
    const https = require(target.protocol === "https:" ? "https" : "http");

    const options = {
      method: "GET",
      hostname: target.hostname,
      port: target.port || (target.protocol === "https:" ? 443 : 80),
      path: target.pathname + target.search,
      headers: {
        Authorization: basicAuthHeader(AUTH),
        Accept: "application/json",
      },
      rejectUnauthorized: false,
    };

    const req = https.request(options, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        const body = Buffer.concat(chunks).toString("utf8");
        resolve({ statusCode: res.statusCode, headers: res.headers, body });
      });
    });

    req.on("error", reject);
    req.end();
  });
}

async function resolveTargetBase() {
  const { URL } = require("url");
  // getip needs no credentials; the gate is the User-Agent. connect.loxonecloud.com answers
  // {"url": "https://<ip-with-dashes>.<SERIAL>.dyndns.loxonecloud.com:<port>"}.
  const resolverUrl = new URL(`${CLOUD_RESOLVER.replace(/\/$/, "")}/getip?snr=${SERIAL}&json=true`);
  const lib = require(resolverUrl.protocol === "https:" ? "https" : "http");

  const options = {
    method: "GET",
    hostname: resolverUrl.hostname,
    port: resolverUrl.port || (resolverUrl.protocol === "https:" ? 443 : 80),
    path: resolverUrl.pathname + resolverUrl.search,
    headers: { "User-Agent": RESOLVER_UA },
    rejectUnauthorized: false,
  };

  return new Promise((resolve, reject) => {
    const req = lib.request(options, (res) => {
      let body = "";
      res.on("data", (chunk) => (body += chunk));
      res.on("end", () => {
        let parsed;
        try {
          parsed = JSON.parse(body);
        } catch (err) {
          reject(new Error(`CloudDNS returned non-JSON: ${body}`));
          return;
        }
        if (!parsed.url) {
          reject(new Error(`CloudDNS did not return a target address: ${body}`));
          return;
        }
        resolve(parsed.url.replace(/\/$/, ""));
      });
    });

    req.on("error", reject);
    req.end();
  });
}

async function callApi(targetBase, path) {
  const base = targetBase.replace(/\/$/, "");
  const fullUrl = `${base}${path}`;
  const response = await httpRequest(fullUrl);
  return response.body;
}

function buildGuest(groupUuid, name, pin, fromUnix, untilUnix) {
  const start = fromUnix !== undefined ? Math.floor(Number(fromUnix)) : Math.floor(Date.now() / 1000);
  const end = untilUnix !== undefined ? Math.floor(Number(untilUnix)) : start + 86400;
  return {
    name,
    userState: 4,
    usergroups: [groupUuid],
    validFrom: toLoxoneEpoch(start),
    validUntil: toLoxoneEpoch(end),
    expirationAction: 1,
    keycodes: [{ code: pin }],
  };
}

async function main() {
  const [, , command, ...rest] = process.argv;
  if (!command) {
    console.error(
      "Usage: node managePin.js <resolve|list-users|show-user|set-pin|clear-pin|list-groups|create-guest|delete-user> [args]"
    );
    process.exit(1);
  }

  if (command === "resolve") {
    console.log(await resolveTargetBase());
    return;
  }

  const targetBase = process.env.TARGET_BASE || (await resolveTargetBase());

  switch (command) {
    case "list-users": {
      console.log(await callApi(targetBase, "/jdev/sps/getuserlist2"));
      break;
    }
    case "show-user": {
      if (!rest[0]) {
        console.error("Usage: show-user <uuid>");
        process.exit(1);
      }
      console.log(await callApi(targetBase, `/jdev/sps/getuser/${rest[0]}`));
      break;
    }
    case "set-pin": {
      if (!rest[0] || !rest[1]) {
        console.error("Usage: set-pin <uuid> <pin>");
        process.exit(1);
      }
      console.log(await callApi(targetBase, `/jdev/sps/updateuseraccesscode/${rest[0]}/${rest[1]}`));
      break;
    }
    case "clear-pin": {
      if (!rest[0]) {
        console.error("Usage: clear-pin <uuid>");
        process.exit(1);
      }
      console.log(await callApi(targetBase, `/jdev/sps/updateuseraccesscode/${rest[0]}/`));
      break;
    }
    case "list-groups": {
      console.log(await callApi(targetBase, "/jdev/sps/getgrouplist"));
      break;
    }
    case "create-guest": {
      if (!rest[0] || !rest[1] || !rest[2]) {
        console.error("Usage: create-guest <group-uuid> <name> <pin> [from-unix] [until-unix]");
        process.exit(1);
      }
      const guest = buildGuest(rest[0], rest[1], rest[2], rest[3], rest[4]);
      const encoded = encodeURIComponent(JSON.stringify(guest));
      console.log(await callApi(targetBase, `/jdev/sps/addoredituser/${encoded}`));
      break;
    }
    case "delete-user": {
      if (!rest[0]) {
        console.error("Usage: delete-user <uuid>");
        process.exit(1);
      }
      console.log(await callApi(targetBase, `/jdev/sps/deleteuser/${rest[0]}`));
      break;
    }
    default:
      console.error(`Unknown command: ${command}`);
      process.exit(1);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
