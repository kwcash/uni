# VPS workflow: cert silos, and the Hostinger move

Written 2026-09-13. Current VPS workload confirmed as **static/generated
course sites only** — no live registrar/database app in production yet.
That fact drives everything below: this is a much cheaper and simpler
migration than it would be if a live app were already serving students.

## 1. Why "silos" and what that means concretely

Each certification should be a self-contained deployment unit, not a
shared blob of pages:

```
/var/www/
├── certs.fstem.net/
│   ├── stat-ml/          <- built from CERT-STAT-ML, static HTML
│   ├── ai-ops/
│   ├── tqm/
│   ├── lean/
│   └── sysa/
├── ms.fstem.net/          <- existing masters tracks, untouched
└── shared/                <- fonts, css, logo — one copy, referenced by all
```

One subdirectory (or subdomain, e.g. `stat-ml.fstem.net`) per cert:

- A cert can be taken offline, rebuilt, or re-priced without touching the
  others — this is the actual meaning of "silo" here, not a separate
  server or VPS per cert. One VPS, one webserver, N static site roots.
- The **build** stays siloed too: `07_export_batch.py` / `04_writeout.py`
  (you already have both) should take `--cert CERT-STAT-ML` and write only
  into that cert's directory. Never a shared "rebuild everything" step
  that can half-fail and leave two certs in an inconsistent state.
- The **database stays out of the silo**. `out/fstem_certs.db` (or its
  production equivalent) is the single source of truth on the VPS,
  read by the build step, not shipped into `/var/www/`.

## 2. Where the capstone fits

The capstone is the one place a cert silo needs something dynamic: a
place for a student to submit a project and a human (or later, a scored
pipeline) to grade it against `project_deliverables`. Two honest options
at your current scale:

- **Cheapest, works today**: capstone submission = an upload form that
  emails you / drops a file into a per-student folder over SFTP, graded
  by hand against the rubric already in `project_deliverables`. Zero new
  infrastructure. Fine for a pilot cohort.
- **Once volume justifies it**: a small registrar app (the "dynamic"
  option you did NOT pick this round) — but don't build that until static
  delivery + manual capstone grading is actually the bottleneck. Building
  it now, before you have paying students, is the over-engineering trap.

## 3. The Hostinger move — sized for what you actually have

Because the workload is static HTML:

- **Any Hostinger VPS tier works**; you are not paying for compute, you're
  paying for storage + bandwidth + the ability to run nginx/Caddy and a
  cron job. Their entry VPS tier (KVM 1 or 2) is enough for 8 cert silos
  of generated HTML plus the masters site. Don't over-buy.
- **Do not point DNS at the new box until content is verified there.**
  The move is: stand up Hostinger VPS → deploy the exact same build
  output there → verify every silo loads → THEN cut over DNS. This makes
  the switch reversible right up to the DNS change, and reversible for a
  TTL window after.

### Concrete sequence

1. **Provision Hostinger VPS.** Point a throwaway subdomain at it
   (e.g. `new.fstem.net`) rather than the real domain, so you can test
   without risk.
2. **Install the serving stack**: nginx (or Caddy, simpler TLS), rsync,
   git. Nothing else — no database server needed for the static tier.
3. **Ship the pipeline**, not just the output: clone this repo
   (`kwcash/uni`) onto the new VPS. The scripts + `out/fstem_certs.db`
   are your source of truth; the built HTML is a disposable artifact you
   can always regenerate there. This also means you're not dependent on
   copying a fragile `/var/www` tree by hand.
4. **Build on the new box**: run the same `0X_*.py` pipeline steps that
   produce `/var/www/certs.fstem.net/*` today. Confirm output byte-size
   and page count match the old VPS before trusting it.
5. **Smoke-test every silo** on `new.fstem.net/stat-ml/` etc. — all 8
   certs plus the masters site, not just one.
6. **Cut DNS over** once verified. Lower your DNS TTL to something short
   (e.g. 300s) a day *before* the cutover so the change propagates fast,
   then flip the A/AAAA records.
7. **Keep the old VPS running read-only for 48-72 hours** as a fallback,
   then cancel it. Don't let the old contract auto-renew into the
   "skyhigh" 2-year price while the new one is being verified — cancel
   or downgrade the old plan explicitly once step 6 is confirmed stable,
   don't just let it lapse.
8. **Automate the rebuild-and-redeploy** as one script
   (`deploy.sh` already exists in spirit per your earlier notes) that:
   runs the builder for a given `--cert`, rsyncs only that cert's output
   directory to the VPS, and reloads nginx. This is what makes "add a
   6th cert later" a non-event instead of a re-migration.

### What NOT to do under deadline pressure

- Don't hand-copy files over the old VPS's control panel — script the
  transfer (rsync/git) so it's repeatable and auditable, same reason the
  db migrations here are scripted rather than hand-edited.
- Don't skip the backup discipline moving to the new box: keep the
  `.bak-<timestamp>` habit for `fstem_certs.db` on the VPS itself, not
  just locally.
- Don't build the dynamic registrar app as part of this move. It's a
  separate, later decision — conflating "move hosting" with "build new
  infrastructure" is how a 2-week VPS swap becomes a 2-month project
  under a 1-month deadline.

## 4. Immediate next step

Point me at the actual `deploy.sh` / `07_export_batch.py` output layout
(or paste what `/var/www/` looks like on the current VPS) and I'll adapt
step 4 above into a real, runnable script rather than the sketch here.
