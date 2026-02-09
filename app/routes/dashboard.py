from flask import Blueprint, render_template, request
from app.models import Project

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    search = request.args.get("q", "").strip()
    sort = request.args.get("sort", "updated")

    query = Project.query
    if search:
        query = query.filter(Project.title.ilike(f"%{search}%"))

    if sort == "title":
        query = query.order_by(Project.title.asc())
    elif sort == "created":
        query = query.order_by(Project.created_at.desc())
    elif sort == "status":
        query = query.order_by(Project.status.asc(), Project.updated_at.desc())
    else:
        query = query.order_by(Project.updated_at.desc())

    projects = query.all()
    return render_template("dashboard.html", projects=projects, search=search, sort=sort)
