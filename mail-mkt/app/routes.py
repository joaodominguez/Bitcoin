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
from .models import Contact, ContactList, ListMember, Suppression

bp = Blueprint("main", __name__)


@bp.get("/health")
def health():
    return {"status": "ok", "app": "mail-mkt"}


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
    if path in ("/health", "/login", "/logout") or path.startswith("/static/"):
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
