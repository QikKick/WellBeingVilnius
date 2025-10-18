from flask import Blueprint, render_template

bp = Blueprint("friends", __name__)


@bp.get("/friends")
def friends():
    return render_template("friends.html", title="WellBeingVilnius")
