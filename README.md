# 🔐 Loxone Cloud – Zarządzanie kodami PIN użytkowników

Dokument opisuje sposób zdalnego zarządzania kodami PIN użytkowników w systemie **Loxone** przez **CloudDNS**, za pomocą prostych komend `curl`.  
Przydatne do integracji z systemami rezerwacji (np. Hotres), które automatycznie nadają kody gościom.

---

## 🧩 Konfiguracja podstawowa

Na początku terminala lub w skrypcie ustaw dane połączenia i dane logowania:

```bash
SERVER_BASE="https://dns.loxonecloud.com/nr-seryjny-miniservera-loxone"
AUTH="login:hasło"
```

---

## 1️⃣ Uzyskanie aktualnego adresu Miniservera (CloudDNS redirect)

CloudDNS nie pośredniczy w ruchu – przekierowuje zapytanie HTTP 307 na dynamiczny adres Twojego Miniservera.

```bash
curl -u "$AUTH" "$SERVER_BASE"
```

Przykładowa odpowiedź:
```
Temporary Redirect. Redirecting to https://195-201-222-243.504F94A10F64.dyndns.loxonecloud.com:37868/
```

Adres z odpowiedzi zapisz jako docelowy:

```bash
TARGET_BASE="https://195-201-222-243.504F94A10F64.dyndns.loxonecloud.com:37868"
```

> ⚠️  Adres i port mogą się zmieniać po restarcie Miniservera — zawsze rozpoczynaj integrację od tego kroku.

---

## 2️⃣ Pobranie listy użytkowników i ich UUID

Lista użytkowników służy do znalezienia `UUID` konta, któremu chcesz nadać lub usunąć kod PIN.

```bash
curl -sk -u "$AUTH" "$TARGET_BASE/jdev/sps/getuserlist2"
```

### Przykładowa odpowiedź:

```
jaaaco@laptop-Jakub ~ % curl -sk -u "$AUTH" "$TARGET_BASE/jdev/sps/getuserlist2"

{"LL": { "control": "dev/sps/getuserlist2", "value": "[{"name":"admin","uuid":"1f8f7500-00c9-e65e-ffff1665f7af6eec","isAdmin":true,"userState":0,"representsControl":false},{"name":"apartament1","uuid":"1f9b57ec-01cb-c459-ffff1665f7af6eec","isAdmin":false,"userState":0,"representsControl":false},{"name":"apartament2","uuid":"1f9b57fa-0077-cffc-ffff1665f7af6eec","isAdmin":false,"userState":0,"representsControl":false},{"name":"s4h","uuid":"1f9b5a3d-0003-10c4-ffff1665f7af6eec","isAdmin":true,"userState":0,"representsControl":false}]", "Code": "200"}}
```

Z powyższej listy:
- `apartament1` – UUID `1f9b57ec-01cb-c459-ffff1665f7af6eec`  
- `apartament2` – UUID `1f9b57fa-0077-cffc-ffff1665f7af6eec`

---

## 3️⃣ Dodanie lub zmiana kodu PIN użytkownika

```bash
USER_UUID="1f9b57ec-01cb-c459-ffff1665f7af6eec"  # apartament1
PIN="123456"  # dozwolone 2–8 cyfr

curl -sk -u "$AUTH" "$TARGET_BASE/jdev/sps/updateuseraccesscode/${USER_UUID}/${PIN}"
```

Po wykonaniu zapytania użytkownik `apartament1` będzie mógł korzystać z kodu **123456**.  
Każde kolejne wywołanie zastępuje poprzedni PIN.

Oczekiwana odpowiedź (fragment):
```json
{"LL":{"value":true,"code":200}}
```

---

## 4️⃣ Sprawdzenie przypisanego PIN-u

Aby sprawdzić, czy kod został dodany:

```bash
curl -sk -u "$AUTH" "$TARGET_BASE/jdev/sps/getuser/${USER_UUID}"
```

### Przykładowa odpowiedź:

```
jaaaco@laptop-Jakub ~ % curl -sk -u "$AUTH" "$TARGET_BASE/jdev/sps/getuser/${USER_UUID}"

{"LL": { "control": "dev/sps/getuser/1f9b57ec-01cb-c459-ffff1665f7af6eec", "value": "{"name":"apartament1","desc":"","uuid":"1f9b57ec-01cb-c459-ffff1665f7af6eec","userState":0,"isAdmin":false,"usergroups":[],"nfcTags":[],"keycodes":[{"code":"CE92D660A16BA5BCC239AE72F93D13EEE746FC8C"}]}", "Code": "200"}}
```

Widzimy, że użytkownik `apartament1` ma przypisany zaszyfrowany kod (hash):
```json
"keycodes": [
  { "code": "CE92D660A16BA5BCC239AE72F93D13EEE746FC8C" }
]
```

Jeśli `keycodes` jest pusty (`[]`), użytkownik **nie ma aktywnego PIN-u**.

---

## 5️⃣ Usunięcie kodu PIN

Aby usunąć kod użytkownika (bez kasowania konta):

```bash
curl -sk -u "$AUTH" "$TARGET_BASE/jdev/sps/updateuseraccesscode/${USER_UUID}/"
```

Po tej operacji sprawdzenie (`getuser/${USER_UUID}`) zwróci:
```json
"keycodes": []
```

czyli brak aktywnego PIN-u.

---

## 🧠 Uwagi praktyczne

- `-s` — tryb cichy (bez postępu)  
- `-k` — pomija weryfikację certyfikatu SSL (CloudDNS używa wildcardów i self-signed certów)  
- PIN działa natychmiast po ustawieniu, nie wymaga restartu Miniservera  
- Każdy użytkownik może mieć tylko **jeden aktywny kod PIN**  
- Jeśli chcesz ustawić okres ważności:  
  - użyj polecenia `addoredituser` z polami `validFrom` i `validUntil`,  
  - lub w **Loxone Config** przypisz użytkownika do **Access Group** z odpowiednim **Time Profile**

---

## ⚙️ Automatyzacja z systemem rezerwacji

Zalecany schemat integracji (np. z PMS/Hotres):

| Moment rezerwacji | Wywoływane polecenie HTTP | Opis |
|-------------------|---------------------------|------|
| Check-in          | `/jdev/sps/updateuseraccesscode/<uuid>/<pin>` | Nadanie PIN |
| (opcjonalnie)     | `/jdev/sps/addoredituser/{uuid,validFrom,validUntil}` | Ustawienie dat ważności |
| Check-out         | `/jdev/sps/updateuseraccesscode/<uuid>/` | Usunięcie PIN |

---

## 🔒 Bezpieczeństwo

- Konto `s4h` powinno mieć wyłącznie uprawnienia do zarządzania użytkownikami i kodami.  
- Ogranicz dostęp do CloudDNS/Miniservera po adresach IP (firewall lub ACL).  
- Synchronizuj zegary PMS i Miniservera (NTP) — to kluczowe przy logice czasowej.  
- Regularnie rotuj hasło serwisowe.

---

## 📄 Podsumowanie

| Akcja | Komenda `curl` |
|-------|----------------|
| **Dodaj PIN** | `curl -sk -u "$AUTH" "$TARGET_BASE/jdev/sps/updateuseraccesscode/<uuid>/<pin>"` |
| **Sprawdź PIN** | `curl -sk -u "$AUTH" "$TARGET_BASE/jdev/sps/getuser/<uuid>"` |
| **Usuń PIN** | `curl -sk -u "$AUTH" "$TARGET_BASE/jdev/sps/updateuseraccesscode/<uuid>/"` |

---

© 2025 – Dokumentacja integracyjna Loxone Cloud (PIN Management)
