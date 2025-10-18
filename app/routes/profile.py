from flask import Blueprint, render_template

bp = Blueprint("profile", __name__)


@bp.get("/profile")
def profile():
    return render_template("profile.html", title="WellBeingVilnius")
