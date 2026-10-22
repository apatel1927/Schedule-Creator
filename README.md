# ShiftLedger — Employee scheduler for Unraid

A self-hosted, calendar-first employee scheduling and time tracking system. One small Docker container; no cloud account, external database, SaaS API, or paid subscription needed.

## Features

- Create multiple stores, assign employees to one or more locations, and filter all calendar views by store.
- Month and week calendars with real dates, future schedules, drafts, publishing and repeating weekday shifts.
- Employee records, jobs, active/inactive status, store-specific hourly rates.
- Owner and manager logins. Owner creates managers and assigns allowed stores. Managers may create/edit schedules, add/edit worked hours, and update per-store hourly rates. Only the owner manages accounts and views payroll summaries.
- Track actual hours separately from scheduled shifts. Enter a time range with unpaid break, or a manual number of hours for *Shopping*, *Pulling orders*, *Delivery*, *Restocking*, *Training*, *Inventory* or *Other* work.
- Gross-pay estimate based on actual recorded hours and the historical rate stored with each time entry. Combines employee hours across all stores for overtime. Monday–Sunday weeks, overtime premium after 40 hours at a weighted-average rate. Export totals to CSV.
- SQLite database kept on a persistent Unraid appdata folder; immutable audit records of changes; separate archive action for time entries and shifts.
- Works on mobile and desktop browsers; no external JavaScript or fonts.

## Install on Unraid

1. Download the project ZIP or clone the repository onto Unraid (or transfer the extracted folder to the server).
2. Open the Unraid **Terminal**, `cd` into the folder containing `INSTALL-UNRAID.sh`, and run:

   ```sh
   sh INSTALL-UNRAID.sh
   ```

3. The first run generates a **strong admin password**, displays it in the terminal and saves it in `.env`. Save that password securely.
4. Visit `http://YOUR-UNRAID-IP:8787`. Sign in as `admin` with the generated password.

The app creates and uses `/mnt/user/appdata/shiftledger/data` for durable database storage. Restarting or updating the container does not erase employee records, shifts or hours. The installation script preserves an existing `.env` and never wipes your appdata.

Prerequisite: Docker Compose availability on Unraid (e.g. Compose Manager / Docker Compose plugin). `INSTALL-UNRAID.sh` checks this before deployment. No container image is published to a registry; Unraid builds the included Dockerfile locally on first install.

**Manual alternative:** copy `.env.example` to `.env`, replace its admin password with a long unique one, and run `docker compose up --build -d` in this directory. The password is only used to create the first owner login on an empty database; changing `.env` later will not reset an existing password.

## Day-to-day workflow

1. Under **Stores**, add locations (the first start includes one `Main Store`).
2. Under **Employees**, create staff, hourly rates and store assignments.
3. Under **Managers & access**, create manager usernames/passwords and their store access.
4. Under **Schedule**, add shifts by date or use **Repeating shifts** to fill future weekdays. Optionally publish planned shifts.
5. Under **Time & tasks**, log actual hours worked. You can use **Record these hours as worked** on a planned shift or enter separate hours for shopping, pulling orders and other duties.
6. Under **Payroll**, select a date range and one store or all stores. Click **Calculate pay**, inspect gross estimates and export CSV.

Schedule entries do **not** automatically count as work. An actual time entry must exist for wages to be included. Time entries capture the pay rate at the time of recording. Updating a future rate does not silently reprice prior hours; owners can intentionally correct a historical rate by editing a time entry.

## Backup & restore

Create a transactionally consistent backup with:

```sh
docker exec shiftledger python /app/backup.py
```

Backups are saved inside `/mnt/user/appdata/shiftledger/data/backups/` with timestamps and a passed SQLite integrity check. **Copy the backups to another device** through your existing Unraid backup workflow. To restore: stop the container, back up the existing database files, replace `/mnt/user/appdata/shiftledger/data/shiftledger.sqlite3` with your chosen verified backup, ensure existing `-wal`/`-shm` files are handled while the container is stopped, then restart. Test restoration on a separate copy before relying on it.

For a routine update, retain `/mnt/user/appdata/shiftledger/data` and `.env`, extract/clone new source to a separate directory, back up first and run `docker compose up -d --build` in that source directory. Avoid running two app containers against the same database.

## Security & limitations

- First boot requires an admin password of 12+ characters; sessions use HttpOnly SameSite cookies and request protection tokens.
- **LAN only by default.** Do **not** forward port 8787 to the internet. For access outside the store, use a trusted VPN or an authenticated reverse proxy with HTTPS. If serving behind HTTPS, set `COOKIE_SECURE=true` in compose after configuring TLS.
- Managers' store access is checked on the server. They can modify pay rates **only for employees at their assigned stores**. Historic rates are snapshot in actual time records.
- Pay is an **estimate**, not payroll processing: no withholding, employer taxes, benefit deductions, tip/bonus adjustments, wage-law jurisdiction logic, exemptions, differential premiums or direct deposits. The default overtime preview assumes 40 hours per Monday–Sunday week, totals all locations, and uses weighted-average regular rates. Confirm the correct employee classification, workweek boundary and overtime calculations with your payroll service before payment.
- Clock-in/clock-out, employee self-service, shift swap requests, printed schedule PDFs and automatic payroll provider integration are not part of v1. Employees do not have logins—owner and manager accounts only.
- SQLite is suitable for a small/medium group of stores with light concurrency. For broad multi-tenant commercial deployment, plan a hardened API, database migrations, external HTTPS, backups and more detailed roles.

## Developer checks

Run `python3 -m unittest discover -s tests -v` from this directory. All Python code uses only the standard library.
