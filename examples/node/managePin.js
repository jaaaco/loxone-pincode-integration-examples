#!/usr/bin/env node
/**
 * Command-line helper for managing Loxone user PIN codes via CloudDNS.
 */

const SERVER_BASE = process.env.SERVER_BASE || "https://dns.loxonecloud.com/504F94A10F64";
const AUTH = process.env.AUTH || "s4h:s4h";

if (!SERVER_BASE || !AUTH) {
  console.error("SERVER_BASE and AUTH must be defined");
  process.exit(1);
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
  const target = new URL(SERVER_BASE);
  const https = require(target.protocol === "https:" ? "https" : "http");

  const options = {
    method: "GET",
    hostname: target.hostname,
    port: target.port || (target.protocol === "https:" ? 443 : 80),
    path: target.pathname + target.search,
    headers: { Authorization: basicAuthHeader(AUTH) },
    rejectUnauthorized: false,
  };

  return new Promise((resolve, reject) => {
    const req = https.request(options, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        resolve(res.headers.location.replace(/\/$/, ""));
      } else {
        resolve(SERVER_BASE.replace(/\/$/, ""));
      }
      res.resume();
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

async function main() {
  const [, , command, arg1, arg2] = process.argv;
  if (!command) {
    console.error("Usage: node managePin.js <resolve|list|show|set|clear> [uuid] [pin]");
    process.exit(1);
  }

  if (command === "resolve") {
    const resolved = await resolveTargetBase();
    console.log(resolved);
    return;
  }

  const targetBase = process.env.TARGET_BASE || (await resolveTargetBase());

  switch (command) {
    case "list": {
      const body = await callApi(targetBase, "/jdev/sps/getuserlist2");
      console.log(body);
      break;
    }
    case "show": {
      if (!arg1) {
        console.error("UUID is required for the show command");
        process.exit(1);
      }
      const body = await callApi(targetBase, `/jdev/sps/getuser/${arg1}`);
      console.log(body);
      break;
    }
    case "set": {
      if (!arg1 || !arg2) {
        console.error("UUID and PIN are required for the set command");
        process.exit(1);
      }
      const body = await callApi(targetBase, `/jdev/sps/updateuseraccesscode/${arg1}/${arg2}`);
      console.log(body);
      break;
    }
    case "clear": {
      if (!arg1) {
        console.error("UUID is required for the clear command");
        process.exit(1);
      }
      const body = await callApi(targetBase, `/jdev/sps/updateuseraccesscode/${arg1}/`);
      console.log(body);
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
