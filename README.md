# Energy Data Pipeline — ENTSO-E

Pipeline automat care colectează date despre sistemul energetic european
(consum, producție, prețuri day-ahead) de pe platforma ENTSO-E Transparency,
le stochează în PostgreSQL și generează analize.

Rulează programat, este idempotent (o rulare repetată nu duplică date) și
descarcă incremental doar intervalul lipsă.

## Arhitectură

```
ENTSO-E API ──> fetch.py ──> transform.py ──> db.py ──> PostgreSQL
                (retry)      (curățare +      (upsert
                             verificări)    idempotent)
                                                 │
                                            report.py ──> analize + grafice
```

Fiecare modul are o singură responsabilitate, iar `transform.py` nu atinge
nici rețeaua, nici baza de date — de aceea poate fi testat direct.

### Schema bazei de date

O singură tabelă, în format „long" (o observație pe rând):

| coloană | descriere |
|---|---|
| `country` | cod ISO (ex. `RO`) |
| `metric` | `load_actual`, `price_day_ahead`, `generation_actual` |
| `psr_type` | tipul resursei la producție (eolian, solar…); gol în rest |
| `ts` | momentul observației (UTC) |
| `value` | valoarea măsurată |
| `unit` | `MW`, `EUR/MWh` |
| `ingested_at` | când a fost preluat rândul |

Constrângere unică pe `(country, metric, psr_type, ts)` — aceasta este cheia
naturală care face `upsert`-ul idempotent.

Motivul formatului long în locul unei coloane per metrică: metricile au
dimensiuni diferite (producția are tip de combustibil, prețul are monedă),
iar adăugarea unei metrici noi nu cere migrarea tabelei.

### Decizii de implementare

- **Watermark, nu re-descărcare completă** — pipeline-ul citește ultimul
  `ts` din baza de date și cere doar intervalul de după el. Excepție:
  prețurile day-ahead sunt publicate în avans, deci watermark-ul lor este
  deja în viitor; pentru ele se cere explicit și ziua următoare.
- **Backfill la prima rulare** — dacă tabela e goală, descarcă ultimele
  `BACKFILL_DAYS` zile.
- **Izolarea erorilor** — o metrică picată nu oprește restul rulării;
  codul de ieșire semnalează dacă au existat eșecuri.
- **Retry cu backoff exponențial** pentru erorile de rețea.
- **Verificări de calitate** — goluri în serie (la pasul dedus din date),
  valori negative, outlieri prin IQR (robust la distribuții asimetrice, cum
  sunt prețurile), calculați separat pe fiecare tip de resursă.
- **Inserare pe transe** — PostgreSQL acceptă cel mult 65535 de parametri
  per statement, iar un backfill de 30 de zile depășește limita.

## Instalare

### Cerințe

- Docker, sau Python 3.12+ și PostgreSQL 14+
- Un token ENTSO-E (gratuit)

### Obținerea tokenului

1. Cont pe <https://transparency.entsoe.eu/>
2. Email la `transparency@entsoe.eu`, cu subiectul `RESTful API access`
   și adresa folosită la înregistrare în corpul mesajului
3. După aprobare, se generează tokenul din setările contului

Aprobarea durează câteva zile lucrătoare.

### Rulare cu Docker

```bash
cp .env.example .env      # completează ENTSOE_API_KEY, DB_USER, DB_PASS, DB_NAME
docker compose up --build
```

### Rulare manuală

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # completează valorile
python -m src.pipeline    # colectează datele
python -m src.report      # generează analizele și graficele în output/
```

### Rulare programată

```bash
crontab -e
# zilnic la 06:00
0 6 * * * /cale/catre/proiect/scripts/run_daily.sh
```

## Demo fără token

Pentru a testa fluxul fără să aștepți aprobarea API-ului, `seed_demo.py`
populează baza cu date sintetice care au tipar zilnic și săptămânal realist:

```bash
DATABASE_URL="sqlite:///demo.db" python seed_demo.py
DATABASE_URL="sqlite:///demo.db" python -m src.report
```

## Teste

```bash
pytest tests/ -v
```

Testele acoperă normalizarea datelor (coloane lipsă, valori nule, DataFrame
gol) și verificările de calitate.

## Analize generate

- profil orar de consum (vârfurile de dimineață și seară)
- comparație zile lucrătoare vs. weekend
- evoluția zilnică a consumului și a prețului

## Ce returnează API-ul în realitate

Confirmat cu `check_api.py` pe `python-entsoe` 0.6.1, pentru `RO`:

| metrică | coloane | unitate |
|---|---|---|
| `load_actual` | `timestamp, value, quantity_unit` | `MAW` |
| `price_day_ahead` | `timestamp, value, currency, price_unit` | `EUR` + `MWH` |
| `generation_actual` | `timestamp, psr_type, value, quantity_unit` | `MAW` |

Trei lucruri de reținut, pentru că toate trei au consecințe în cod:

- **Rezoluția este de 15 minute**, nu orară. Verificarea de goluri deduce
  pasul din date (`transform.infer_step`) în loc să-l presupună.
- **Unitățile sunt coduri UN/CEFACT** (`MAW` = megawatt). Sunt citite din
  răspuns și traduse în notația uzuală, nu luate dintr-o constantă din cod.
- **`psr_type` amestecă denumiri și coduri brute**: `python-entsoe` traduce
  doar B01–B20, așa că `B25` (Energy storage) rămâne cod. Cum `psr_type`
  face parte din cheia naturală, o schimbare a tabelei de traduceri din
  pachet ar produce rânduri paralele pentru aceeași resursă.

## Limitări cunoscute

- Analiza pe tipuri de producție (`psr_type`) este stocată, dar nu încă
  raportată.
- Prețurile negative sunt normale pe piața day-ahead, dar verificarea de
  calitate le numără la fel pentru toate metricile.
