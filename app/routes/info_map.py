from flask import Blueprint, render_template

bp = Blueprint("infomap", __name__)


@bp.get("/info-map")
def infomap():
    return render_template("info_map.html", title="WellBeingVilnius")
