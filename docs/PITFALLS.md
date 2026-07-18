# Dziennik inżynierski — integracja PIN/kody Loxone przez CloudDNS

Surowe ustalenia z testów end-to-end na żywym Miniserverze. Wszystko poniżej było
odpalone na prawdziwym sprzęcie i zwróciło `Code=200` (albo `404` tam, gdzie
oczekiwane). To jest „dlaczego akurat tak" do skróconej sekcji pułapek w README.

Konfiguracja testowa jest zanonimizowana — numery seryjne, UUID-y i kody w
przykładach są wymyślone.

---

## 1. Model integracji: statyczni userzy vs grupa + user efemeryczny

Pierwsze podejście: **N stałych użytkowników** (`pokoj-01..N`), PMS tylko
nadaje/kasuje im kod. Proste, ale ma dwa ograniczenia:

1. Jeden aktywny kod na użytkownika — brak nakładających się rezerwacji.
2. **Blokada zapisu configu** (patrz §5).

Drugie podejście (docelowe dla PMS z samoobsługą): **grupy dostępowe** (`ap1..N`,
pokój N = grupa apN), a PMS zakłada **użytkownika per rezerwacja**:

```
getgrouplist                → UUID grupy pokoju
addoredituser {obiekt}      → nowy user w grupie, z kodem i oknem ważności
getuser {uuid}              → weryfikacja
deleteuser {uuid}           → skasowanie (albo auto-kasowanie po validUntil)
```

Cały scenariusz przeszedł na koncie serwisowym: wszystkie kroki `Code=200`,
`deleteuser` → następny `getuser` daje `404`.

---

## 2. `addoredituser` — format i pułapka pustego `keycodes`

Obiekt użytkownika przekazujesz jako **JSON zakodowany URL-em w ścieżce**:

```
/jdev/sps/addoredituser/{percent-encoded-json}
```

Minimalny obiekt nowego gościa (brak `uuid` = nowy user):

```json
{
  "name": "rez-88231",
  "userState": 4,
  "usergroups": ["<uuid-grupy>"],
  "validFrom":  683737200,
  "validUntil": 683996400,
  "expirationAction": 1,
  "keycodes": [ { "code": "246813" } ]
}
```

- Edycja istniejącego: dołóż `"uuid": "..."`.
- `userState: 4` — user aktywny w oknie czasowym.
- `expirationAction: 1` — auto-kasowanie po `validUntil`.

**Pułapka:** `addoredituser` potrafi utworzyć użytkownika z **pustym**
`"keycodes": []` mimo intencji nadania kodu. Dwie równoważne, sprawdzone drogi:

1. `keycodes: [{"code":"..."}]` **inline** w obiekcie (jak wyżej), albo
2. założenie usera bez kodu, potem `updateuseraccesscode/{uuid}/{PIN}`.

Zawsze rób `getuser` po założeniu i sprawdź, czy `keycodes` nie jest puste.

---

## 3. Hash kodu nie jest solony per-użytkownik

Ten sam PIN u dwóch różnych użytkowników → **identyczny hash**. Zweryfikowane:
kod `135791` u dwóch userów dał ten sam `500E385A...`. Czyli hash nie jest solony
nazwą ani UUID użytkownika.

Konsekwencja bezpieczeństwa:
- krótkie / współdzielone PIN-y są podatne na atak tęczową tablicą, jeśli wycieknie
  lista userów z `keycodes`,
- generuj **długie, losowe** kody per rezerwacja, nie recykluj między gośćmi.

---

## 4. Epoch Loxone: sekundy od 2009-01-01 00:00:00 UTC

`validFrom` / `validUntil` NIE są uniksowym czasem. To sekundy od
`2009-01-01 00:00:00 UTC`. Stała różnicy względem unixa:

```
LOXONE_EPOCH_OFFSET = 1230768000   # unix seconds at 2009-01-01T00:00:00Z
loxone_ts = unix_ts - 1230768000
unix_ts   = loxone_ts + 1230768000
```

Potwierdzenie: porównano `validFrom` ustawiony w teście z polem `lastEdit`
(stemplowanym zegarem Miniservera). Delta wyszła równa czasowi trwania testu
(10–13 s), a **nie** 7200 s — co wyklucza interpretację „epoch w czasie lokalnym
CEST". Baza to UTC.

Częsty błąd: podanie surowego `date +%s` jako `validFrom` → okno ważności
przesunięte o ~15 lat w przód, kod „nigdy nie działa".

---

## 5. Stali userzy z API bez hasła blokują zapis konfiguracji

Użytkownik założony przez API nie ma hasła (`changePassword=true`, `scorePWD=-1`).
Gdy takich userów jest w bazie kilka/kilkanaście, **Loxone Config przy próbie
zapisu configu** zgłasza „niezgodne hasła" i **odmawia zapisu**.

- Pole `password` w `addoredituser` bywa przyjmowane (`scorePWD` −1 → −2), ale nie
  zawsze kasuje `changePassword` — nie polegaj na tym.
- Praktyczny wniosek: przy wielu drzwiach preferuj **Model B** (userzy efemeryczni,
  kasowani po pobycie / auto-kasowani), żeby baza się nie zapychała stałymi userami
  z API i nie blokowała edycji configu.
- Jeśli masz już zablokowany zapis: skasuj stałych userów z API ręcznie w Config.

---

## 6. `getgrouplist` — kolejność i grupy systemowe

- Lista **nieposortowana** (obserwacja: `ap14` na końcu, nie na swoim miejscu).
- Zawiera **2 grupy systemowe** poza grupami pokoi.

Zawsze **filtruj po nazwie**, nie po indeksie tablicy. Zbuduj mapę
`nazwa → uuid` raz i cache'uj po stronie PMS.

---

## 7. Uprawnienie do klawiatur idzie przez Access Control w Config

`usergroups` daje userowi przynależność do grupy, ale to, do których klawiatur /
wejść faktycznie otwiera, ustawiają **bloki Access Control w Loxone Config**
przypięte do grupy. Samo API nie nadaje tego mapowania.

Zweryfikuj fizycznie na jednym pokoju (grupa `ap1` → właściwa klawiatura + ew.
wejście główne / szlaban), zanim wpuścisz PMS na wszystkie drzwi.

---

## 8. `value` jest podwójnie zakodowany

Odpowiedzi `getuserlist2` / `getgrouplist` / `getuser` mają strukturę:

```json
{ "LL": { "control": "...", "value": "<STRING-Z-JSON-EM>", "Code": "200" } }
```

`LL.value` to **string**, w którym siedzi kolejny JSON (lista userów / obiekt
usera). Parsuj dwustopniowo: najpierw kopertę `LL`, potem `JSON.parse(LL.value)`.
`Code` bywa stringiem (`"200"`) w jednych endpointach i liczbą (`200`) w innych —
porównuj elastycznie.

---

## 9. CloudDNS: adres zmienny, zawsze resolwuj od nowa

CloudDNS odpowiada **HTTP 307** na dynamiczny adres Miniservera (host + port).
Adres i port zmieniają się po restarcie Miniservera. Każdą sesję integracji
zaczynaj od kroku `resolve`; nie hardkoduj `TARGET_BASE` na stałe po stronie PMS.
