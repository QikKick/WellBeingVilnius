from flask import Flask


def create_app() -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__, template_folder="templates", static_folder="static")

    # Basic config
    app.config.from_mapping(SECRET_KEY="dev")

    # Register blueprints
    from .routes.main import bp as home_bp
    from .routes.dev import bp as dev_bp

    app.register_blueprint(home_bp)
    app.register_blueprint(dev_bp)

    return app
