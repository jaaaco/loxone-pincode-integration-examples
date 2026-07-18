# Zdalne kody PIN w Loxone sterowane z systemu rezerwacji (PMS)

Gość rezerwuje pobyt, dostaje jednorazowy kod, wchodzi klawiaturą przy drzwiach.
Po wymeldowaniu kod przestaje działać sam. Bez recepcji, bez wydawania kart, bez
człowieka w pętli. Cała logika siedzi w Miniserverze Loxone, a system rezerwacji
(PMS) steruje nim zwykłymi żądaniami HTTP przez CloudDNS. Żadnej chmury pośrednika,
żadnego mostka „w tle".

**TLDR**

| | |
|---|---|
| Co to robi | PMS nadaje i kasuje gościom kody PIN do drzwi, zdalnie |
| Jak steruje | zwykły HTTP GET do Miniservera przez CloudDNS (HTTP 307 → adres na żywo) |
| Dwa modele | stały user na drzwi **albo** user na rezerwację z datą ważności i auto-kasowaniem |
| Zależność od chmury | tylko CloudDNS jako książka adresowa; ruch idzie prosto do Miniservera |
| Sprzęt pośredni | brak (nie ma Raspberry, serwera, mostka) |
| Języki przykładów | Bash, Python, Node — ten sam zestaw komend |

To jest kompletny zapis realnej integracji obiektu na samoobsłudze (hostel /
apartamenty na wynajem, meldunek przez kiosk): jakich endpointów użyć, w jakiej
kolejności, i na czym się przejechać. Nie teoria — każdy krok był odpalony na
żywym Miniserverze i zwrócił `Code=200`. Sekcja z pułapkami niżej to jest to, za
co normalnie się płaci, bo każda z nich kosztowała parę godzin diagnostyki.

> Jeden z serii poradników smart home dla samodzielnych od
> [smart.hyte.pl](https://smart.hyte.pl). Materiały, wsparcie i konsultacje na końcu.

---

## Dlaczego przez API Loxone, a nie „gotowy" zamek hotelowy?

Bo gotowy system zamków hotelowych to osobna infrastruktura: własny kontroler,
własne karty, własna licencja, i najczęściej własna chmura, przez którą wszystko
przechodzi. Płacisz abonament i modlisz się, żeby producent nie zwinął serwerów.

Jeśli obiekt i tak stoi na Loxone (ogrzewanie per pokój, szlaban, oświetlenie
wspólne), to Miniserver **już** umie trzymać użytkowników z kodami PIN na
klawiaturach NFC. Wystarczy, że PMS będzie te kody nadawał i kasował automatycznie
w rytm rezerwacji. Zero dodatkowego sprzętu, zero drugiej chmury, jeden system do
utrzymania.

Minus jest jeden i trzeba go znać z góry: interfejs, przez który się to robi
(`jdev/sps/...`), jest słabo udokumentowany i ma kilka nieoczywistych zachowań
(epoch liczony od 2009 roku, hash kodu bez soli, blokada zapisu konfiguracji).
Wszystkie są niżej, rozpisane, żebyś nie tracił na nie czasu.

---

## Co budujemy — dwa modele integracji

Są dwie drogi. Wybór zależy od tego, ile masz drzwi i jak często się zmieniają
goście.

### Model A — stały użytkownik na drzwi (proste, mało drzwi)

Jeden użytkownik Loxone = jedne drzwi (`pokoj-01`, `pokoj-02`, ...). PMS przy
zameldowaniu **ustawia** kod PIN temu użytkownikowi, przy wymeldowaniu go
**czyści**. Użytkownicy istnieją na stałe, zmienia się tylko ich aktywny kod.

- ➕ Najprościej: dwa wywołania (`set-pin`, `clear-pin`), zero zarządzania cyklem życia.
- ➕ UUID drzwi są stałe — PMS zna je raz na zawsze.
- ➖ Jeden aktywny PIN na użytkownika naraz (nie obsłużysz dwóch nakładających się rezerwacji tych samych drzwi).
- ➖ **Uwaga na blokadę zapisu konfiguracji** (patrz pułapki) — przy wielu stałych userach założonych przez API Loxone Config potrafi odmówić zapisu.

### Model B — użytkownik na rezerwację, w grupie, z datą ważności (skalowalne)

Drzwi to nie użytkownik, tylko **grupa dostępowa** (`ap1`, `ap2`, ...). Na każdą
rezerwację PMS **zakłada nowego użytkownika**, wrzuca go do grupy odpowiadającej
pokojowi, nadaje kod i okno ważności (`validFrom` / `validUntil`). Po wygaśnięciu
użytkownik kasuje się sam (`expirationAction=1`), albo PMS kasuje go jawnie po
wymeldowaniu.

- ➕ Obsługa nakładających się rezerwacji, historii, wielu kodów na te same drzwi.
- ➕ Kod ma twarde okno czasowe — działa tylko w trakcie pobytu.
- ➕ Baza użytkowników sama się czyści (auto-kasowanie po `validUntil`).
- ➖ Więcej ruchomych części: trzeba znać UUID grupy, umieć złożyć obiekt użytkownika, ogarnąć epoch Loxone.

To jest model, którego chce większość PMS-ów (m.in. te robiące samoobsługowy
check-in). Reszta poradnika pokazuje oba.

---

## Czego potrzebujesz

| Element | Co konkretnie |
|---|---|
| Miniserver Loxone | z klawiaturami NFC / zamkami kodowymi przy drzwiach, dostępny przez CloudDNS |
| Konto serwisowe | osobny użytkownik na Miniserverze z prawem zarządzania userami i kodami (nie admin) |
| Numer seryjny | Miniservera (MAC bez dwukropków, np. `AABBCCDDEEFF`) |
| CloudDNS włączony | żeby dojść do Miniservera bez stałego publicznego IP |
| `curl` / Python 3 / Node | do odpalenia przykładów (każdy robi to samo) |

Konto serwisowe zakładasz w **Loxone Config → Użytkownicy**. Daj mu tylko tyle
uprawnień, ile trzeba (zarządzanie użytkownikami i kodami), i nic więcej. Hasło
trzymaj poza repo — patrz `.env.example`.

---

## Konfiguracja krok po kroku

Skopiuj `.env.example` do `.env.local`, wpisz swoje dane i wczytaj:

```bash
cp .env.example .env.local
# uzupełnij SERVER_BASE i AUTH
source .env.local
```

```bash
SERVER_BASE="https://dns.loxonecloud.com/AABBCCDDEEFF"   # AABBCCDDEEFF = numer seryjny
AUTH="serwis:twoje-haslo"                                 # konto serwisowe
```

### 1. Znajdź aktualny adres Miniservera (CloudDNS redirect)

CloudDNS nie pośredniczy w ruchu — na zapytanie odpowiada przekierowaniem HTTP 307
na aktualny, dynamiczny adres Twojego Miniservera. **Zawsze zaczynaj integrację od
tego kroku**, bo adres i port zmieniają się po restarcie Miniservera.

```bash
./examples/bash/pin_workflow.sh resolve
# → https://203-0-113-42.AABBCCDDEEFF.dyndns.loxonecloud.com:37868
```

Ten adres (`TARGET_BASE`) trzymaj w pamięci na czas sesji i wołaj pod niego
kolejne endpointy. Skrypty resolwują go automatycznie, jeśli nie ustawisz
`TARGET_BASE` ręcznie.

### 2. Pobierz listę użytkowników i ich UUID

Do nadania/skasowania kodu potrzebujesz `UUID` użytkownika, nie jego nazwy.

```bash
./examples/bash/pin_workflow.sh list-users
```

```json
{"LL":{"control":"dev/sps/getuserlist2","value":"[
  {\"name\":\"admin\",\"uuid\":\"11111111-1111-1111-ffff000000000000\",\"isAdmin\":true},
  {\"name\":\"pokoj-01\",\"uuid\":\"22222222-2222-2222-ffff000000000000\",\"isAdmin\":false},
  {\"name\":\"pokoj-02\",\"uuid\":\"33333333-3333-3333-ffff000000000000\",\"isAdmin\":false}
]","Code":"200"}}
```

> `value` przychodzi jako **string z JSON-em w środku** (podwójnie zakodowany).
> Najpierw parsujesz kopertę `LL.value`, potem parsujesz to, co w niej jest.

---

## Model A — stały użytkownik na drzwi

### A1. Nadaj / zmień kod PIN

```bash
./examples/bash/pin_workflow.sh set-pin 22222222-2222-2222-ffff000000000000 123456
```

Kod działa natychmiast, bez restartu Miniservera. Każde kolejne wywołanie
**zastępuje** poprzedni PIN (jeden aktywny kod na użytkownika). Dozwolone 2–8 cyfr.

```json
{"LL":{"value":true,"code":200}}
```

### A2. Sprawdź, czy kod siedzi

```bash
./examples/bash/pin_workflow.sh show-user 22222222-2222-2222-ffff000000000000
```

```json
"keycodes": [ { "code": "CE92D660A16BA5BCC239AE72F93D13EEE746FC8C" } ]
```

Kod jest zahashowany (nie da się go odczytać z powrotem). Pusta tablica
`"keycodes": []` = użytkownik nie ma aktywnego PIN-u.

### A3. Skasuj kod (bez kasowania konta)

```bash
./examples/bash/pin_workflow.sh clear-pin 22222222-2222-2222-ffff000000000000
```

Po tym `show-user` zwróci `"keycodes": []`. Konto zostaje, tylko traci kod.

**Spięcie z PMS (Model A):** check-in → `set-pin`, check-out → `clear-pin`.
Dwa webhooki i gotowe.

---

## Model B — użytkownik na rezerwację, w grupie

### B1. Pobierz listę grup i znajdź UUID grupy pokoju

```bash
./examples/bash/pin_workflow.sh list-groups
```

```json
{"LL":{"control":"dev/sps/getgrouplist","value":"[
  {\"name\":\"ap1\",\"uuid\":\"aaaa0001-0000-0000-ffff000000000000\"},
  {\"name\":\"ap2\",\"uuid\":\"aaaa0002-0000-0000-ffff000000000000\"},
  {\"name\":\"(admin)\",\"uuid\":\"00000000-0000-0000-0000000000000000\"}
]","Code":"200"}}
```

> **Filtruj po nazwie, nie po indeksie.** Lista bywa nieposortowana (`ap14` potrafi
> wylądować na końcu) i zawiera 2 grupy systemowe. Szukaj grupy pokoju po jej
> nazwie, nie zakładaj kolejności.

### B2. Załóż użytkownika na pobyt (w grupie, z kodem i oknem ważności)

Jedno wywołanie `addoredituser` zakłada gościa, wrzuca do grupy pokoju, nadaje kod
i ustawia okno czasowe. Brak `uuid` w obiekcie = **nowy** użytkownik.

```bash
# create-guest <uuid-grupy> <nazwa> <pin> [od-unix] [do-unix]
./examples/bash/pin_workflow.sh create-guest \
  aaaa0001-0000-0000-ffff000000000000 \
  rez-88231 \
  246813 \
  $(date -d '2026-08-01 15:00' +%s) \
  $(date -d '2026-08-04 11:00' +%s)
```

Skrypt składa i wysyła taki obiekt (daty przeliczone na epoch Loxone — patrz niżej):

```json
{
  "name": "rez-88231",
  "userState": 4,
  "usergroups": ["aaaa0001-0000-0000-ffff000000000000"],
  "validFrom": 683737200,
  "validUntil": 683996400,
  "expirationAction": 1,
  "keycodes": [ { "code": "246813" } ]
}
```

- `userState: 4` — użytkownik aktywny w oknie czasowym.
- `expirationAction: 1` — **auto-kasowanie** po `validUntil` (baza sama się czyści).
- `keycodes` inline — nadaje kod od razu. (Alternatywnie: załóż bez kodu i dołóż
  `set-pin` — patrz pułapka o `addoredituser`.)
- `validFrom` / `validUntil` — **epoch Loxone**, czyli sekundy od `2009-01-01
  00:00:00 UTC`. Skrypt sam odejmuje `1230768000` od uniksowego czasu. Nie podawaj
  tu surowego `date +%s`.

### B3. Zweryfikuj i (opcjonalnie) skasuj

```bash
./examples/bash/pin_workflow.sh show-user <uuid-nowego-gościa>   # keycodes + daty
./examples/bash/pin_workflow.sh delete-user <uuid-nowego-gościa> # jawne skasowanie
```

Po `delete-user` kolejne `show-user` zwróci `404` — użytkownika nie ma. Jeśli
zaufałeś `expirationAction=1`, kasowanie po wymeldowaniu jest zbędne, ale i tak
warto raz zweryfikować, że auto-kasowanie działa na Twoim firmware.

**Spięcie z PMS (Model B):**
`getgrouplist` (raz, cache UUID grup) → przy rezerwacji `addoredituser`
(nowy gość, kod, okno) → po pobycie `deleteuser` **albo** nic (auto-kasowanie).

---

## Na czym się przejechaliśmy (żebyś Ty nie musiał)

To jest najcenniejsza część. Każdy punkt kosztował realny czas na żywym systemie.

- **`addoredituser` nie nadaje kodu sam z siebie.** Tworzy użytkownika z pustym
  `"keycodes": []`, nawet jeśli myślisz, że go podałeś złym polem. Działają dwie
  równoważne drogi: pole `keycodes: [{"code":"..."}]` **inline** w obiekcie
  `addoredituser`, albo osobne `updateuseraccesscode/{uuid}/{PIN}` po założeniu.
  Zweryfikuj `getuser` po każdym założeniu — cichy pusty `keycodes` to najczęstszy błąd.

- **Hash kodu nie jest solony per-użytkownik.** Ten sam PIN u dwóch różnych
  użytkowników daje **identyczny hash** (sprawdzone: `500E385A...` dla `135791`
  u obu). Wniosek bezpieczeństwa: nie używaj krótkich ani współdzielonych PIN-ów —
  wyciek bazy userów = atak tęczową tablicą. Generuj długie, losowe kody per rezerwacja.

- **Epoch to `2009-01-01 00:00:00 UTC`, nie unix i nie czas lokalny.**
  `validFrom` / `validUntil` liczą sekundy od 1 stycznia 2009 UTC. Potwierdzone
  porównaniem z `lastEdit` stemplowanym zegarem Miniservera (delta = czas trwania
  testu, nie 7200 s przesunięcia strefy). Pomylisz strefę albo bazę epocha → kod
  zacznie/skończy działać o godziny obok. Stała do odjęcia od unixa: **`1230768000`**.

- **`getgrouplist` zwraca nieposortowaną listę + 2 grupy systemowe.** Nie adresuj
  grup po indeksie tablicy — filtruj po nazwie. `ap14` potrafi być na końcu, nie na
  swoim miejscu.

- **Stali userzy z API bez hasła potrafią zablokować zapis konfiguracji.** Jeśli
  założysz przez API użytkowników bez hasła (`changePassword=true`), Loxone Config
  przy próbie zapisu configu wywala „niezgodne hasła" i **nie pozwala zapisać**.
  Trzeba ich skasować ręcznie w Config. To jeden z powodów, dla których przy wielu
  drzwiach **Model B** (userzy efemeryczni, kasowani po pobycie) jest zdrowszy niż
  armia stałych userów z API — baza się nie zapycha i nie blokuje edycji configu.

- **Uprawnienie do klawiatury idzie przez grupę i bloki Access Control w Config,
  nie przez samo API.** Założenie usera w grupie (`usergroups`) daje mu
  przynależność, ale to, do których klawiatur / wejść faktycznie otwiera, ustawiasz
  blokami Access Control w Loxone Config. Zweryfikuj fizycznie na jednym pokoju,
  zanim wpuścisz PMS na całość.

Pełny dziennik (surowe odpowiedzi, wersje, ślepe uliczki) jest w
[`docs/PITFALLS.md`](docs/PITFALLS.md). Jak lubisz wiedzieć „dlaczego akurat tak",
to tam.

---

## Skrypty i struktura projektu

Trzy implementacje tego samego zestawu komend — wybierz język, który masz pod ręką:

```
examples/
  bash/pin_workflow.sh     komendy przez curl (zero zależności)
  python/manage_pin.py     to samo na czystej bibliotece standardowej
  node/managePin.js        to samo na czystym Node (bez npm install)
tests/
  test_scripts.py          testy end-to-end wszystkich trzech na atrapie Miniservera
docs/
  PITFALLS.md              pełny dziennik inżynierski (epoch, hash, blokada Config)
.env.example               szablon konfiguracji (skopiuj do .env.local)
```

| Komenda | Endpoint Loxone | Do czego |
|---|---|---|
| `resolve` | CloudDNS 307 | aktualny adres Miniservera |
| `list-users` | `getuserlist2` | użytkownicy + UUID |
| `show-user <uuid>` | `getuser/<uuid>` | szczegóły + `keycodes` |
| `set-pin <uuid> <pin>` | `updateuseraccesscode/<uuid>/<pin>` | nadaj/zmień kod (Model A) |
| `clear-pin <uuid>` | `updateuseraccesscode/<uuid>/` | skasuj kod (Model A) |
| `list-groups` | `getgrouplist` | grupy dostępowe + UUID (Model B) |
| `create-guest <grupa> <nazwa> <pin> [od] [do]` | `addoredituser` | gość na rezerwację w grupie (Model B) |
| `delete-user <uuid>` | `deleteuser/<uuid>` | skasuj użytkownika (Model B) |

Wszystkie trzy skrypty przyjmują `SERVER_BASE` i `AUTH` ze środowiska (albo
argumentów w Pythonie) i domyślnie startują od `resolve`. Odpowiedzi zwracają
surowo — parsowanie zostawiamy Twojej integracji.

Uwagi do `curl` w skryptach:
- `-s` — tryb cichy (bez paska postępu)
- `-k` — pomija weryfikację certyfikatu (CloudDNS używa wildcardów / self-signed)

---

## Bezpieczeństwo

- **Konto serwisowe ≠ admin.** Nadaj mu tylko zarządzanie użytkownikami i kodami.
  Wyciek konta z pełnym adminem to wyciek całego budynku.
- **Ogranicz dostęp do Miniservera po IP** (firewall / ACL na routerze), jeśli
  możesz. CloudDNS to tylko książka adresowa — sam nie chroni.
- **Synchronizuj zegary PMS i Miniservera (NTP).** Cała logika czasowa (`validFrom`
  / `validUntil`) leży, jeśli zegary się rozjeżdżają.
- **Generuj długie, losowe PIN-y per rezerwacja** (patrz: hash bez soli). Nie
  recykluj kodów między gośćmi.
- **Nie commituj `.env.local`.** Jest w `.gitignore`. Do repo trafia tylko
  `.env.example` z placeholderami.

---

## Robisz smart home sam? Jesteśmy po tej samej stronie

Ten projekt to jeden z serii poradników dla ludzi, którzy montują i programują
sami. Zamysł jest prosty: kompletny przepis dostajesz za darmo, a jak
potrzebujesz, dokładamy dwie rzeczy, których z samego bloga nie wyciśniesz.

**Sprzęt Loxone, jeden koszyk.** Miniserver, klawiatury NFC, ekstensje —
z rabatem partnerskim, dobrane pod Twój obiekt. Zamiast składać zamówienie po
omacku dostajesz komplet, który zagra za pierwszym razem.
[Sklep: smart.hyte.pl](https://smart.hyte.pl)

**Wsparcie, kiedy utkniesz.** Płatna konsultacja zdalna rozliczana godzinowo albo
sporadyczna poprawka integracji PMS ↔ Loxone. Nie sprzedajemy abonamentu na coś,
co i tak działa. Płacisz, gdy realnie potrzebujesz.
[smart.hyte.pl](https://smart.hyte.pl)

**Więcej do czytania.** Kolejne poradniki, realizacje z twardymi liczbami i baza
wiedzy o integracjach Loxone.
[Realizacje](https://smart.hyte.pl/realizacje) · [smart.hyte.pl](https://smart.hyte.pl)

Partner techniczny, nie podwykonawca. Kolega z branży, nie handlowiec. Jak coś
nie ma sensu, powiemy to wprost.

---

## Licencja

MIT. Rób co chcesz, tylko nie miej pretensji, jak coś nie zadziała. Przetestuj na
swoim Miniserverze — każdy firmware jest trochę inny.
