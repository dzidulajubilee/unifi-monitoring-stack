"""Reports portal: sign in with a Grafana account, generate and download reports,
and (Grafana admins) set up email and the schedule.

One process runs the web app, a single worker that builds reports one at a
time, and the scheduler, which drops due reports into the same queue.
"""

import base64
import datetime as dt
import itertools
import json
import logging
import os
import queue
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from functools import wraps

from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

from . import api as R
from . import config

GRAFANA = os.environ.get("GRAFANA_URL", "http://grafana:3000")
log = logging.getLogger("portal")

app = Flask(
    __name__,
    static_folder=str(config.ROOT / "fonts"),
    static_url_path="/fonts",
    template_folder=str(config.ROOT / "templates" / "portal"),
)


def _secret():
    """Session key kept in the output folder so logins survive restarts."""
    f = R.OUT / ".portal-secret"
    if not f.exists():
        R.OUT.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_hex(32))
        os.chmod(f, 0o600)
    return f.read_text().strip()


app.config.update(
    SECRET_KEY=_secret(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=dt.timedelta(hours=12),
)


# ---------------------------------------------------------------- jobs

jobs = {}  # id -> job dict, newest kept
work = queue.Queue()
_ids = itertools.count(1)
_lock = threading.Lock()


def enqueue(kind, start, end, by, sends=(), login=None):
    """sends: [(style, addresses)], style "full" or "summary"."""
    with _lock:
        jid = next(_ids)
        jobs[jid] = {
            "id": jid,
            "kind": kind,
            "start": start,
            "end": end,
            "by": by,
            "sends": list(sends),
            "to": ", ".join(to for _, to in sends),
            "login": login,
            "status": "queued",
            "file": None,
            "error": None,
            "queued_at": dt.datetime.now(config.TZ),
        }
        for old in sorted(jobs)[:-30]:
            jobs.pop(old)
    work.put(jid)
    return jid


def worker():
    while True:
        jid = work.get()
        job = jobs.get(jid)
        if not job:
            continue
        job["status"] = "running"
        kind, by, login = job["kind"], job["by"], job.get("login")
        what = R.report_title(kind, job["start"], job["end"])
        who = "automatically" if by == "schedule" else f"by {by}"
        try:
            pdfs, subject, lines, errors = R.build(kind, job["end"], job["start"] if kind == "custom" else None)
        except Exception as e:  # noqa: BLE001 - reported, worker keeps going
            log.exception("job %s failed", jid)
            job.update(status="failed", error=str(e) or e.__class__.__name__)
            R.notify(f"{what} could not be made ({who}): {job['error']}", "error", by=by)
            continue
        full = pdfs["full"].name
        job.update(status="done", file=full)
        if errors:  # Prometheus or Loki didn't answer some queries: those figures show as –
            R.notify(
                f"{what} was made {who}, but {errors} figure{'s' * (errors != 1)} couldn't be fetched "
                "from Prometheus or Loki and show as –. Check those services and make it again",
                "error",
                by=by,
                file=full,
            )
        results = R.deliver(pdfs, subject, lines, job["sends"]) if R.load_settings()["smtp"].get("host") else []
        sent = R.describe_sends(results)
        R.notify(f"{what} was made {who}" + (f" and {sent}" if sent else ""), by=by, file=full)
        for style, to, err in results:
            if err:
                job["error"] = f"Email failed: {err}"
                # addresses and mail errors go to admins and the person who asked
                R.notify(
                    f"Emailing the {'summary' if style == 'summary' else 'full'} {what[0].lower() + what[1:]} "
                    f"to {to} failed: {err}. The report is saved here",
                    "error",
                    by=by,
                    file=full,
                    admins_only=True,
                    for_user=login,
                )


def scheduler():
    while True:
        try:
            busy = {(j["kind"], j["end"]) for j in jobs.values() if j["status"] in ("queued", "running")}
            for kind, end in R.due_reports():
                if (kind, end) not in busy:
                    enqueue(kind, R.period_start(kind, end), end, "schedule", R.recipients(kind))
        except Exception:  # noqa: BLE001
            log.exception("scheduler tick failed")
        time.sleep(300)


# ---------------------------------------------------------------- auth

_fails = {}  # ip -> [timestamps]


def grafana_login(user, password):
    """Check the credentials against Grafana. Returns (name, is_admin, login) or None."""
    token = base64.b64encode(f"{user}:{password}".encode()).decode()

    def get(path):
        req = urllib.request.Request(GRAFANA + path, headers={"Authorization": f"Basic {token}"})
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.load(r)

    try:
        me = get("/api/user")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return None
        raise
    orgs = get("/api/user/orgs")
    admin = bool(me.get("isGrafanaAdmin")) or any(o.get("role") == "Admin" for o in orgs)
    return me.get("name") or me.get("login") or user, admin, me.get("login") or user


def login_required(admin=False):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if "user" not in session:
                return redirect(url_for("login", next=request.path))
            if admin and not session.get("admin"):
                abort(403)
            if request.method == "POST" and request.form.get("csrf") != session.get("csrf"):
                abort(400)
            return fn(*a, **kw)

        return wrapper

    return deco


@app.context_processor
def common():
    st = R.load_settings()
    return {
        "user": session.get("user"),
        "is_admin": session.get("admin"),
        "csrf": session.get("csrf"),
        "name": R.display_name(st),
        "custom_name": bool(st["general"]["name"].strip()),
        "tz": str(config.TZ),
    }


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = request.remote_addr
        recent = [t for t in _fails.get(ip, []) if t > time.time() - 300]
        if len(recent) >= 5:
            error = "Too many failed attempts. Try again in 5 minutes"
        else:
            try:
                res = grafana_login(request.form.get("username", "").strip(), request.form.get("password", ""))
            except Exception:  # noqa: BLE001
                log.exception("grafana login check failed")
                res, error = None, "Can't reach Grafana to check your login. Try again shortly"
            if res:
                session.clear()
                session.permanent = True
                session.update(user=res[0], admin=res[1], login=res[2], csrf=secrets.token_hex(16))
                _fails.pop(ip, None)
                nxt = request.args.get("next", "")
                return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("index"))
            if not error:
                _fails[ip] = recent + [time.time()]
                error = "Wrong username or password"
    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------------------------------------------------------------- reports

NAME = re.compile(
    r"^(weekly|monthly|quarterly|custom)-(\d{4}-\d{2}-\d{2})(?:-to-(\d{4}-\d{2}-\d{2}))?(-summary)?\.pdf$"
)


def describe(kind, start, last):
    if kind == "custom":
        return "Custom", f"{start:%-d %b %Y}" if start == last else f"{start:%-d %b} – {last:%-d %b %Y}"
    end = last + dt.timedelta(days=1)
    s = dt.datetime.combine(start, dt.time(), config.TZ)
    e = dt.datetime.combine(end, dt.time(), config.TZ)
    title, dates = R.period_label(kind, s, e)
    return kind.capitalize(), f"{title} · {dates}"


def archive():
    out = []
    for f in R.OUT.glob("*.pdf"):
        m = NAME.match(f.name)
        if not m or m[4]:  # summaries are listed with their full report
            continue
        kind, start = m[1], dt.date.fromisoformat(m[2])
        if m[3]:
            last = dt.date.fromisoformat(m[3])
        else:
            s = dt.datetime.combine(start, dt.time(), config.TZ)
            last = _period_last(kind, s)
        label, period = describe(kind, start, last)
        st = f.stat()
        summary = f.with_name(f.stem + "-summary.pdf")
        out.append(
            {
                "file": f.name,
                "summary": summary.name if summary.exists() else None,
                "kind": label,
                "period": period,
                "start": start,
                "made": dt.datetime.fromtimestamp(st.st_mtime, config.TZ),
                "size": st.st_size,
            }
        )
    return sorted(out, key=lambda r: (r["start"], r["made"]), reverse=True)


def _period_last(kind, start):
    """Last day of the period that begins at `start`."""
    if kind == "weekly":
        return (start + dt.timedelta(days=6)).date()
    months = 1 if kind == "monthly" else 3
    m = start.month + months
    nxt = start.replace(year=start.year + (m - 1) // 12, month=(m - 1) % 12 + 1)
    return (nxt - dt.timedelta(days=1)).date()


def job_view(j):
    label, period = (
        describe(j["kind"], j["start"].date(), (j["end"] - dt.timedelta(days=1)).date())
        if j["kind"] != "custom" or j["end"].time() == dt.time()
        else ("Custom", f"{j['start']:%-d %b} – now")
    )
    return {
        "id": j["id"],
        "kind": label,
        "period": period,
        "status": j["status"],
        "file": j["file"],
        "error": j["error"],
        "by": j["by"],
        "to": j["to"],
        "queued_at": j["queued_at"].strftime("%H:%M"),
    }


@app.get("/")
@login_required()
def index():
    s = R.load_settings()
    today = dt.datetime.now(config.TZ).date()
    return render_template(
        "reports.html",
        reports=archive(),
        jobs=active_jobs(),
        periods=period_choices(),
        recipients=s["recipients"],
        recipients_summary=s["recipients_summary"],
        today=today,
        schedule=schedule_text(s["schedule"]),
        week_ago=today - dt.timedelta(days=7),
        mail_ready=bool(s["smtp"].get("host")),
    )


def period_choices():
    """The two periods offered for each report type, with their real dates."""
    now = dt.datetime.now(config.TZ)
    out = {}
    for kind in ("weekly", "monthly", "quarterly"):
        end = R.period_end(kind, now)
        last = R.period_start(kind, end)
        before = R.period_start(kind, last)

        def label(s, e, kind=kind):
            title, dates = R.period_label(kind, s, e)
            return f"{title} · {dates}" if kind in ("weekly", "quarterly") else title

        out[kind] = {
            "last": label(last, end),
            "previous": label(before, last),
            "current": label(end, R._next_period(kind, end)),
        }
    return out


def schedule_text(sch):
    when = {
        "weekly": "weekly reports every Monday",
        "monthly": "monthly reports on the 1st",
        "quarterly": "quarterly reports on the first day of each quarter",
    }
    parts = [when[k] for k in ("weekly", "monthly", "quarterly") if k in sch.get("kinds", [])]
    if not sch.get("enabled") or not parts:
        return "Automatic reports are off. You can make one yourself"
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Made automatically: {text}, at {int(sch.get('hour', 7)):02d}:00. You can also make one yourself"


@app.post("/generate")
@login_required()
def generate():
    f = request.form
    kind = f.get("kind")
    R.load_settings()  # applies the configured time zone
    now = dt.datetime.now(config.TZ)
    day = lambda x: dt.datetime.combine(dt.date.fromisoformat(x), dt.time(), config.TZ)
    try:
        if kind == "custom":
            start, last = day(f["start"]), day(f["end"])
            if last < start:
                raise ValueError("the end date is before the start date")
            if (last - start).days > 366:
                raise ValueError("pick a range of a year or less")
            end = min(last + dt.timedelta(days=1), now)
        elif kind in ("weekly", "monthly", "quarterly"):
            end = R.period_end(kind, now)
            if f.get("which") == "previous":
                end = R.period_start(kind, end)
            start = R.period_start(kind, end)
        else:
            raise ValueError("unknown report type")
    except (KeyError, ValueError) as e:
        flash(f"Can't generate that report: {e}")
        return redirect(url_for("index"))
    to = f.get("to", "").strip() if f.get("email") else ""
    if to and not R.load_settings()["smtp"].get("host"):
        flash("Email isn't set up yet, so the report will only be saved here")
        to = ""
    style = "summary" if f.get("style") == "summary" else "full"
    enqueue(kind, start, end, session["user"], [(style, to)] if to else [], login=me())
    return redirect(url_for("index"))


def active_jobs():
    """Only reports still waiting or being made; finished ones go to notifications."""
    return [job_view(j) for j in reversed(jobs.values()) if j["status"] in ("queued", "running")]


@app.get("/jobs")
@login_required()
def jobs_json():
    return jsonify(active_jobs())


# ---------------------------------------------------------------- notifications


def me():
    return session.get("login") or session.get("user")


def when(iso):
    t = dt.datetime.fromisoformat(iso).astimezone(config.TZ)
    secs = (dt.datetime.now(config.TZ) - t).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 6 * 3600:
        return f"{int(secs // 3600)} h ago"
    return t.strftime("%-d %b %H:%M")


@app.get("/notifications")
@login_required()
def notifications():
    notes = R.notes_for(me(), session.get("admin"))
    return jsonify(
        {
            "unread": sum(not n["read"] for n in notes),
            "items": [
                {
                    "id": n["id"],
                    "text": n["text"],
                    "level": n["level"],
                    "read": n["read"],
                    "when": when(n["t"]),
                    "file": n["file"] if n.get("file") and (R.OUT / n["file"]).exists() else None,
                }
                for n in notes[:50]
            ],
        }
    )


@app.post("/notifications/read")
@login_required()
def notifications_read():
    if request.form.get("all"):
        ids = [n["id"] for n in R.notes_for(me(), session.get("admin"))]
    else:
        ids = request.form.getlist("id")
    R.mark_read(me(), ids)
    return notifications()


@app.post("/notifications/clear")
@login_required()
def notifications_clear():
    R.clear(me(), [n["id"] for n in R.notes_for(me(), session.get("admin"))])
    return notifications()


@app.get("/download/<name>")
@login_required()
def download(name):
    if not NAME.match(name) or not (R.OUT / name).exists():
        abort(404)
    return send_from_directory(R.OUT, name, as_attachment=request.args.get("view") is None)


@app.post("/delete/<name>")
@login_required(admin=True)
def delete(name):
    if NAME.match(name) and (R.OUT / name).exists():
        (R.OUT / name).unlink()
        twin = (R.OUT / name).with_name(name[:-4] + "-summary.pdf")
        if twin.exists():
            twin.unlink()
        flash(f"Deleted {name}")
        R.notify(f"{session['user']} deleted {name}", by=session["user"], admins_only=True)
    return redirect(url_for("index"))


# ---------------------------------------------------------------- settings


def dns_servers_from(form, saved):
    """The DNS server rows on the Settings form. A blank password keeps the saved one; a row with no
    address is dropped. Returns (servers, problem)."""
    out = []
    for i in range(int(form.get("dns_rows") or 0)):

        def g(k, i=i):
            return form.get(f"dns_{k}_{i}", "").strip()

        url = g("url").rstrip("/")
        if not url or form.get(f"dns_remove_{i}"):
            continue
        if not re.match(r"^https?://[^\s/]+", url):
            return (
                None,
                f"The DNS server address {url!r} must start with http:// or https://, for example http://10.0.0.53",
            )
        kind = g("kind") if g("kind") in R.dns.KINDS else "pihole"
        old = saved[i] if i < len(saved) else {}
        out.append(
            {
                "kind": kind,
                "name": g("name")[:40] or R.dns.KINDS[kind],
                "url": url,
                "secret": form.get(f"dns_secret_{i}") or (old.get("secret", "") if old.get("url") == url else ""),
                "verify": not form.get(f"dns_noverify_{i}"),
            }
        )
    return out, None


@app.route("/settings/dns-test", methods=["POST"])
@login_required(admin=True)
def dns_test():
    """Test one server row from the Settings form without saving anything. A blank password uses the
    one saved for that row, as long as its address hasn't changed."""
    f = request.form
    url = f.get("url", "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+", url):
        return jsonify(ok=False, message="Enter an address starting with http:// or https://")
    saved = R.load_settings()["dns"].get("servers", [])
    try:
        old = saved[int(f.get("i", -1))]
    except (ValueError, IndexError):
        old = {}
    kind = f.get("kind") if f.get("kind") in R.dns.KINDS else "pihole"
    secret = f.get("secret") or (old.get("secret", "") if old.get("url") == url else "")
    why = R.dns.test({"kind": kind, "url": url, "secret": secret, "verify": not f.get("noverify")})
    return jsonify(ok=not why, message=f"{R.dns.KINDS[kind]} answered" if not why else why[0].upper() + why[1:])


@app.route("/settings", methods=["GET", "POST"])
@login_required(admin=True)
def settings():
    s = R.load_settings()
    if request.method == "POST":
        f = request.form
        try:
            port = int(f.get("port") or 587)
            hour = int(f.get("hour") or 7)
            if not 0 <= hour <= 23:
                raise ValueError
        except ValueError:
            flash("Port and send hour must be numbers (hour 0 to 23)")
            return redirect(url_for("settings"))
        sections = {k: bool(f.get(f"sec_{k}")) for k, *_ in R.SECTIONS}
        sections_summary = {k: bool(f.get(f"sum_{k}")) for k, *_ in R.SECTIONS}
        if not f.get("dns_shown"):  # the DNS tick is hidden until a server is added: keep what was saved
            sections["dns"] = s["sections"].get("dns", True)
            sections_summary["dns"] = s["sections_summary"].get("dns", False)
        servers, problem = dns_servers_from(f, s["dns"].get("servers", []))
        if problem:
            flash(problem)
            return redirect(url_for("settings"))
        if not any(sections.values()) or not any(sections_summary.values()):
            flash("Pick at least one thing to include in both the full report and the summary")
            return redirect(url_for("settings"))
        s["general"]["name"] = f.get("name", "").strip()[:60]
        s["general"]["grafana_links"] = bool(f.get("grafana_links"))
        gurl = f.get("grafana_url", "").strip().rstrip("/")
        if gurl and not re.match(r"^https?://[^\s/]+", gurl):
            flash("The Grafana address must start with http:// or https://, for example http://10.0.0.5:3000")
            return redirect(url_for("settings"))
        s["general"]["grafana_url"] = gurl
        tz = f.get("timezone", "").strip()
        if tz not in R.timezones():
            flash(f"Unknown time zone {tz!r}. Pick one from the list, e.g. Europe/London or UTC")
            return redirect(url_for("settings"))
        s["general"]["timezone"] = tz
        s["sections"] = sections
        s["sections_summary"] = sections_summary
        s["dns"]["servers"] = servers
        s["dns"]["enabled"] = bool(f.get("dns_enabled"))
        s["smtp"].update(
            host=f.get("host", "").strip(),
            port=port,
            security=f.get("security", "starttls"),
            user=f.get("user", "").strip(),
            **{"from": f.get("from", "").strip()},
        )
        if f.get("password"):  # blank keeps the saved password
            s["smtp"]["password"] = f["password"]
        if f.get("clear_password"):
            s["smtp"]["password"] = ""
        for k in ("weekly", "monthly", "quarterly"):
            s["recipients"][k] = ", ".join(R.split_addresses(f.get(f"to_{k}", "")))
            s["recipients_summary"][k] = ", ".join(R.split_addresses(f.get(f"to_s_{k}", "")))
        s["schedule"].update(
            enabled=bool(f.get("enabled")),
            hour=hour,
            kinds=[k for k in ("weekly", "monthly", "quarterly") if f.get(f"auto_{k}")],
        )
        R.save_settings(s)
        log.info("settings changed by %s", session["user"])
        R.notify(f"Settings were changed by {session['user']}", by=session["user"], admins_only=True)
        flash("Settings saved")
        if f.get("action") == "test":
            to = f.get("test_to", "").strip()
            try:
                R.send_mail(
                    to,
                    f"{R.display_name()}: test email from the reports portal",
                    "This is a test from the reports portal. Email is working.",
                )
                flash(f"Test email sent to {to}")
            except Exception as e:  # noqa: BLE001
                flash(f"Test email failed: {R.mail_error(e)}")
        return redirect(url_for("settings"))
    grafana_guess = f"{request.scheme}://{request.host.split(':')[0]}:3000"  # Grafana usually runs next to the portal
    return render_template(
        "settings.html",
        s=s,
        has_password=bool(s["smtp"].get("password")),
        dns_rows=s["dns"].get("servers", []),
        dns_enabled=s["dns"].get("enabled", True),
        dns_kinds=R.dns.KINDS,
        has_dns=bool(R.dns.servers(s)),
        sections=R.SECTIONS,
        grafana_guess=grafana_guess,
        timezones=R.timezones(),
    )


# ---------------------------------------------------------------- main


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    from waitress import serve

    port = int(os.environ.get("PORTAL_PORT", 8088))
    log.info("portal on :%d, signing in through %s", port, GRAFANA)
    serve(app, host="0.0.0.0", port=port, threads=8)


if __name__ == "__main__":
    main()
