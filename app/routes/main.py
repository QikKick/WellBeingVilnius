from flask import Blueprint, render_template

bp = Blueprint("main", __name__)


@bp.get("/")
def home_page():
    return render_template("index.html", title="WellBeingVilnius")
