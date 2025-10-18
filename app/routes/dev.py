from flask import Blueprint, render_template, request, jsonify
import os, time, threading, requests
from functools import lru_cache

bp = Blueprint("dev", __name__, template_folder="templates")

@bp.get("/dev")
def home_page():
    return render_template("dev.html")