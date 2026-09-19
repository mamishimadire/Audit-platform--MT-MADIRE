# Audit Data Gateway

Runs inside the client's own network. It never accepts inbound connections —
every call it makes is outbound, to the platform's API. It connects to the
client's database using read-only credentials that never leave this machine.

## Quick start (recommended)

Download this package from the platform's Gateways screen ("Download for
Windows" / "Download for Linux"), unzip it, then from inside the folder:

```bash
run.bat  https://your-platform.example.com/api/v1 AT7F-9K2D-4B6M   # Windows
./run.sh https://your-platform.example.com/api/v1 AT7F-9K2D-4B6M   # Linux/macOS
```

That creates the virtual environment, installs dependencies, registers the
Gateway with the code from the Gateways screen, and copies
`config.example.yaml` to `config.yaml` for you to edit. Then skip to step 2
below.

## Manual install

```bash
python -m venv .venv
.venv\Scripts\activate        # or: source .venv/bin/activate
pip install -r requirements.txt
```

## 1. Register

Get a registration code from the platform's Gateways screen (valid for 15
minutes, single use), then:

```bash
python -m gateway.register --url https://your-platform.example.com/api/v1 --code AT7F-9K2D-4B6M
```

This saves `.gateway_identity.json` — the Gateway's long-lived device
credential. Keep it out of version control (already gitignored).

## 2. Configure

```bash
cp config.example.yaml config.yaml
```

Edit `config.yaml`: set `platform_url`, and for each connection paste the
`connection_id` shown on the Data Sources screen after you register a data
source there and attach this gateway. Prefer `password_env` over a
plaintext `password` in the file.

### Column profiling (what leaves this machine)

Once a day (`profile_interval_hours`) the gateway samples up to
`profile_sample_rows` rows from each table and sends the platform a small
**summary** of each column: how many were empty, how many distinct values, and
whether it holds dates, numbers or text. This lets the platform check a column
holds the right *kind* of data, not just the right name.

Real values are sent **only** for small, non-sensitive, enum-like columns such
as a `status` (at most 20 distinct values, each at most 40 characters). They are
never sent for a column named like a password, email, phone, address, salary,
bank/card detail or similar, and never for identifiers, dates or free text. The
platform screens every summary again on arrival and can only store less than
what is sent.

Turn it off with `profiling_enabled: false`, or for one connection with
`profiling: false`. Tests: `.venv\Scripts\python.exe -m unittest discover -s tests`.

## 3. Run

```bash
python -m gateway.main --config config.yaml            # one pass: test, discover, heartbeat
python -m gateway.main --config config.yaml --loop      # keep running on the configured interval
```

For production, schedule the one-pass form via Task Scheduler / cron / a
systemd timer rather than running `--loop` as a bare foreground process.

## Adding a database engine

Each engine is one class in `gateway/connectors/` that builds a SQLAlchemy
connection URL — schema discovery itself is implemented once, generically,
via SQLAlchemy reflection in `connectors/base.py`. Add the class, register
it in `connectors/__init__.py`'s `_REGISTRY`, done.
