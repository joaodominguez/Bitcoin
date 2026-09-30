from __future__ import annotations

from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from sqlalchemy import func, or_, select

from .auth import credentials_configured, is_authenticated, login_required
from .config import Config
from .db import SessionLocal
from .import_csv import import_contacts
from .models import Campaign, Contact, ContactList, ListMember, Message, Suppression
from .sender import enqueue_campaign, send_test_email
from .tokens import parse_unsubscribe_token
from .unione import UnioneClient, UnioneError

bp = Blueprint("main", __name__)


@bp.get("/health")
def health():
    client = UnioneClient()
    return {
        "status": "ok",
        "app": "mail-mkt",
        "unione_configured": client.configured,
        "unione_base": Config.UNIONE_BASE_URL,
        "from_email": Config.UNIONE_FROM_EMAIL or None,
    }


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not credentials_configured():
        return redirect(url_for("main.contacts"))
    if is_authenticated() and request.method == "GET":
        return redirect(request.args.get("next") or url_for("main.contacts"))

    error = None
    username = Config.DASHBOARD_USER
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        if username == Config.DASHBOARD_USER and password == Config.DASHBOARD_PASSWORD:
            session["mailmkt_auth"] = True
            session.permanent = True
            nxt = request.args.get("next") or url_for("main.contacts")
            if not nxt.startswith("/"):
                nxt = url_for("main.contacts")
            return redirect(nxt)
        error = "Utilizador ou password incorretos."
    return render_template("login.html", error=error, username=username)


@bp.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.before_app_request
def _guard():
    path = request.path or "/"
    public = (
        path in ("/health", "/login", "/logout", "/webhooks/unione")
        or path.startswith("/static/")
        or path.startswith("/u/")
    )
    if public:
        return None
    if not credentials_configured():
        return None
    if is_authenticated():
        return None
    if path.startswith("/api/"):
        return {"error": "Autenticação necessária", "login": "/login"}, 401
    return redirect(url_for("main.login", next=path))


@bp.get("/")
@login_required
def home():
    return redirect(url_for("main.contacts"))


def _stats(session):
    total = session.scalar(select(func.count()).select_from(Contact)) or 0
    active = session.scalar(
        select(func.count()).select_from(Contact).where(Contact.status == "active")
    ) or 0
    unsub = session.scalar(
        select(func.count()).select_from(Contact).where(Contact.status == "unsubscribed")
    ) or 0
    suppressed = session.scalar(select(func.count()).select_from(Suppression)) or 0
    lists = session.scalar(select(func.count()).select_from(ContactList)) or 0
    return {
        "total": total,
        "active": active,
        "unsubscribed": unsub,
        "suppressed": suppressed,
        "lists": lists,
    }


@bp.get("/contacts")
@login_required
def contacts():
    q = (request.args.get("q") or "").strip()
    status = (request.args.get("status") or "").strip()
    list_id = request.args.get("list_id", type=int)
    page = max(1, request.args.get("page", 1, type=int))
    per_page = Config.PAGE_SIZE

    with SessionLocal() as session:
        stmt = select(Contact)
        count_stmt = select(func.count()).select_from(Contact)

        if q:
            like = f"%{q.lower()}%"
            filt = or_(
                func.lower(Contact.email).like(like),
                func.lower(func.coalesce(Contact.name, "")).like(like),
                func.lower(func.coalesce(Contact.tags, "")).like(like),
            )
            stmt = stmt.where(filt)
            count_stmt = count_stmt.where(filt)
        if status:
            stmt = stmt.where(Contact.status == status)
            count_stmt = count_stmt.where(Contact.status == status)
        if list_id:
            stmt = stmt.join(ListMember, ListMember.contact_id == Contact.id).where(
                ListMember.list_id == list_id
            )
            count_stmt = count_stmt.select_from(Contact).join(
                ListMember, ListMember.contact_id == Contact.id
            ).where(ListMember.list_id == list_id)

        total = session.scalar(count_stmt) or 0
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = session.scalars(
            stmt.order_by(Contact.created_at.desc(), Contact.id.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()
        lists = session.scalars(select(ContactList).order_by(ContactList.name)).all()
        stats = _stats(session)

        return render_template(
            "contacts.html",
            contacts=rows,
            lists=lists,
            stats=stats,
            q=q,
            status=status,
            list_id=list_id,
            page=page,
            pages=pages,
            total=total,
            per_page=per_page,
        )


@bp.route("/import", methods=["GET", "POST"])
@login_required
def import_page():
    with SessionLocal() as session:
        lists = session.scalars(select(ContactList).order_by(ContactList.name)).all()
        if request.method == "GET":
            return render_template("import.html", lists=lists, result=None)

        upload = request.files.get("file")
        if not upload or not upload.filename:
            flash("Escolhe um ficheiro CSV.", "error")
            return render_template("import.html", lists=lists, result=None)

        list_name = (request.form.get("list_name") or "").strip() or None
        existing_list = request.form.get("existing_list") or ""
        if existing_list and not list_name:
            lst = session.get(ContactList, int(existing_list))
            list_name = lst.name if lst else None
        source = (request.form.get("source") or "csv").strip() or "csv"
        update_existing = request.form.get("update_existing") == "1"

        result = import_contacts(
            session,
            upload.stream,
            list_name=list_name,
            default_source=source,
            update_existing=update_existing,
        )
        session.commit()
        flash(
            f"Import concluído: {result.created} novos, {result.updated} atualizados.",
            "ok",
        )
        lists = session.scalars(select(ContactList).order_by(ContactList.name)).all()
        return render_template("import.html", lists=lists, result=result)


@bp.post("/contacts/<int:contact_id>/unsubscribe")
@login_required
def unsubscribe_contact(contact_id: int):
    with SessionLocal() as session:
        contact = session.get(Contact, contact_id)
        if contact:
            contact.status = "unsubscribed"
            if not session.scalar(
                select(Suppression).where(Suppression.email == contact.email)
            ):
                session.add(
                    Suppression(
                        email=contact.email,
                        reason="unsubscribe",
                        note="Marcado na UI",
                    )
                )
            session.commit()
            flash(f"{contact.email} marcado como unsubscribed.", "ok")
    return redirect(request.referrer or url_for("main.contacts"))


@bp.get("/lists")
@login_required
def lists_page():
    with SessionLocal() as session:
        lists = session.scalars(select(ContactList).order_by(ContactList.name)).all()
        counts = {}
        for lst in lists:
            counts[lst.id] = session.scalar(
                select(func.count()).select_from(ListMember).where(
                    ListMember.list_id == lst.id
                )
            ) or 0
        return render_template("lists.html", lists=lists, counts=counts)


@bp.route("/lists/new", methods=["POST"])
@login_required
def lists_new():
    name = (request.form.get("name") or "").strip()
    description = (request.form.get("description") or "").strip() or None
    if not name:
        flash("Nome da lista obrigatório.", "error")
        return redirect(url_for("main.lists_page"))
    with SessionLocal() as session:
        if session.scalar(select(ContactList).where(ContactList.name == name)):
            flash("Já existe uma lista com esse nome.", "error")
        else:
            session.add(ContactList(name=name, description=description))
            session.commit()
            flash("Lista criada.", "ok")
    return redirect(url_for("main.lists_page"))


@bp.get("/suppressions")
@login_required
def suppressions_page():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = Config.PAGE_SIZE
    with SessionLocal() as session:
        total = session.scalar(select(func.count()).select_from(Suppression)) or 0
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = session.scalars(
            select(Suppression)
            .order_by(Suppression.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        ).all()
        return render_template(
            "suppressions.html",
            rows=rows,
            page=page,
            pages=pages,
            total=total,
        )


@bp.post("/suppressions")
@login_required
def suppressions_add():
    email = (request.form.get("email") or "").strip().lower()
    reason = (request.form.get("reason") or "manual").strip()
    with SessionLocal() as session:
        from .import_csv import normalize_email

        email = normalize_email(email)
        if not email:
            flash("Email inválido.", "error")
        elif session.scalar(select(Suppression).where(Suppression.email == email)):
            flash("Já está na suppression list.", "error")
        else:
            session.add(Suppression(email=email, reason=reason))
            contact = session.scalar(select(Contact).where(Contact.email == email))
            if contact:
                contact.status = "suppressed"
            session.commit()
            flash("Email adicionado à suppression list.", "ok")
    return redirect(url_for("main.suppressions_page"))


DEFAULT_HTML = """<div style="font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#222">
  <p>Olá {{name}},</p>
  <p>Escreve aqui a tua mensagem.</p>
  <p>Cumprimentos,<br>{{from_name}}</p>
</div>"""


@bp.get("/campaigns")
@login_required
def campaigns():
    with SessionLocal() as session:
        rows = session.scalars(select(Campaign).order_by(Campaign.id.desc()).limit(100)).all()
        lists = session.scalars(select(ContactList).order_by(ContactList.name)).all()
        client = UnioneClient()
        return render_template(
            "campaigns.html",
            campaigns=rows,
            lists=lists,
            unione_ok=client.configured,
            from_email=Config.UNIONE_FROM_EMAIL,
            default_html=DEFAULT_HTML.replace("{{from_name}}", Config.UNIONE_FROM_NAME),
        )


@bp.post("/campaigns")
@login_required
def campaigns_create():
    name = (request.form.get("name") or "").strip()
    subject = (request.form.get("subject") or "").strip()
    html_body = (request.form.get("html_body") or "").strip()
    list_id = request.form.get("list_id", type=int)
    action = request.form.get("action") or "draft"
    if not name or not subject or not html_body:
        flash("Nome, assunto e HTML são obrigatórios.", "error")
        return redirect(url_for("main.campaigns"))
    with SessionLocal() as session:
        camp = Campaign(
            name=name,
            subject=subject,
            html_body=html_body,
            list_id=list_id or None,
            from_email=Config.UNIONE_FROM_EMAIL or None,
            from_name=Config.UNIONE_FROM_NAME or None,
            status="draft",
        )
        session.add(camp)
        session.flush()
        if action == "queue":
            try:
                enqueue_campaign(session, camp)
                flash(f"Campanha «{name}» enfileirada ({camp.total} destinatários).", "ok")
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                flash(f"Erro ao enfileirar: {exc}", "error")
                return redirect(url_for("main.campaigns"))
        else:
            flash("Rascunho guardado.", "ok")
        session.commit()
        return redirect(url_for("main.campaign_detail", campaign_id=camp.id))


@bp.get("/campaigns/<int:campaign_id>")
@login_required
def campaign_detail(campaign_id: int):
    with SessionLocal() as session:
        camp = session.get(Campaign, campaign_id)
        if not camp:
            flash("Campanha não encontrada.", "error")
            return redirect(url_for("main.campaigns"))
        counts = {
            s: session.scalar(
                select(func.count()).select_from(Message).where(
                    Message.campaign_id == camp.id, Message.status == s
                )
            ) or 0
            for s in ("pending", "sent", "failed", "skipped")
        }
        recent = session.scalars(
            select(Message)
            .where(Message.campaign_id == camp.id)
            .order_by(Message.id.desc())
            .limit(30)
        ).all()
        lst = session.get(ContactList, camp.list_id) if camp.list_id else None
        return render_template(
            "campaign_detail.html",
            campaign=camp,
            counts=counts,
            recent=recent,
            list_name=lst.name if lst else "Todos os ativos",
            unione_ok=UnioneClient().configured,
        )


@bp.post("/campaigns/<int:campaign_id>/queue")
@login_required
def campaign_queue(campaign_id: int):
    with SessionLocal() as session:
        camp = session.get(Campaign, campaign_id)
        if not camp:
            flash("Campanha não encontrada.", "error")
            return redirect(url_for("main.campaigns"))
        try:
            enqueue_campaign(session, camp)
            session.commit()
            flash(f"Enfileirada: {camp.total} destinatários. O worker vai enviar via UniOne.", "ok")
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            flash(f"Erro: {exc}", "error")
    return redirect(url_for("main.campaign_detail", campaign_id=campaign_id))


@bp.post("/campaigns/<int:campaign_id>/pause")
@login_required
def campaign_pause(campaign_id: int):
    with SessionLocal() as session:
        camp = session.get(Campaign, campaign_id)
        if camp and camp.status in ("queued", "sending"):
            camp.status = "paused"
            session.commit()
            flash("Campanha pausada.", "ok")
    return redirect(url_for("main.campaign_detail", campaign_id=campaign_id))


@bp.post("/campaigns/test")
@login_required
def campaigns_test():
    to_email = (request.form.get("test_email") or "").strip().lower()
    subject = (request.form.get("subject") or "Teste mail-mkt").strip()
    html_body = (request.form.get("html_body") or DEFAULT_HTML).strip()
    if not to_email or "@" not in to_email:
        flash("Email de teste inválido.", "error")
        return redirect(url_for("main.campaigns"))
    try:
        result = send_test_email(to_email=to_email, subject=subject, html=html_body)
        flash(f"Teste enviado para {to_email} (job_id={result.get('job_id')}).", "ok")
    except UnioneError as exc:
        flash(f"UniOne: {exc}", "error")
    except Exception as exc:  # noqa: BLE001
        flash(f"Erro: {exc}", "error")
    return redirect(url_for("main.campaigns"))


@bp.get("/settings")
@login_required
def settings():
    client = UnioneClient()
    ping = None
    ping_error = None
    if client.api_key:
        try:
            ping = client.ping()
        except Exception as exc:  # noqa: BLE001
            ping_error = str(exc)
    return render_template(
        "settings.html",
        configured=client.configured,
        has_key=bool(client.api_key),
        base_url=Config.UNIONE_BASE_URL,
        from_email=Config.UNIONE_FROM_EMAIL,
        from_name=Config.UNIONE_FROM_NAME,
        public_base=Config.PUBLIC_BASE_URL,
        batch_size=Config.UNIONE_BATCH_SIZE,
        ping=ping,
        ping_error=ping_error,
    )


@bp.get("/u/<token>")
def public_unsubscribe(token: str):
    data = parse_unsubscribe_token(token)
    if not data:
        return render_template("unsubscribed.html", ok=False, email=None), 400
    email = data["email"]
    with SessionLocal() as session:
        contact = session.scalar(select(Contact).where(Contact.email == email))
        if contact:
            contact.status = "unsubscribed"
        if not session.scalar(select(Suppression).where(Suppression.email == email)):
            session.add(
                Suppression(email=email, reason="unsubscribe", note="Link público")
            )
        session.commit()
    return render_template("unsubscribed.html", ok=True, email=email)


@bp.post("/webhooks/unione")
def unione_webhook():
    """Receive UniOne event notifications (configure URL in UniOne panel)."""
    payload = request.get_json(silent=True) or {}
    events = payload.get("events") or payload.get("event_data") or []
    if isinstance(payload, dict) and payload.get("event_name") and not events:
        events = [payload]
    if not isinstance(events, list):
        events = [events]

    with SessionLocal() as session:
        for ev in events:
            if not isinstance(ev, dict):
                continue
            event_name = str(ev.get("event_name") or ev.get("event") or "").lower()
            email = (ev.get("email") or "").strip().lower()
            if not email:
                continue
            contact = session.scalar(select(Contact).where(Contact.email == email))
            msg_id = None
            meta = ev.get("metadata") or {}
            if isinstance(meta, dict) and meta.get("message_id"):
                try:
                    msg_id = int(meta["message_id"])
                except (TypeError, ValueError):
                    msg_id = None
            message = session.get(Message, msg_id) if msg_id else None

            if event_name in ("unsubscribed", "unsubscribe"):
                if contact:
                    contact.status = "unsubscribed"
                _ensure_suppression(session, email, "unsubscribe", event_name)
                if message:
                    message.status = "skipped"
            elif event_name in ("hard_bounced", "soft_bounced", "bounced"):
                if contact and event_name == "hard_bounced":
                    contact.status = "bounced"
                    _ensure_suppression(session, email, "bounce", event_name)
                if message:
                    message.status = "bounced"
            elif event_name in ("spam", "complained"):
                if contact:
                    contact.status = "suppressed"
                _ensure_suppression(session, email, "complaint", event_name)
                if message:
                    message.status = "complained"
            elif event_name in ("opened", "open"):
                if message and message.status == "sent":
                    message.status = "opened"
            elif event_name in ("clicked", "click"):
                if message and message.status in ("sent", "opened"):
                    message.status = "clicked"
        session.commit()
    return {"status": "ok"}


def _ensure_suppression(session, email: str, reason: str, note: str) -> None:
    if not session.scalar(select(Suppression).where(Suppression.email == email)):
        session.add(Suppression(email=email, reason=reason, note=note))
