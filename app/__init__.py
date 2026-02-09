import os
from flask import Flask, flash, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from werkzeug.exceptions import RequestEntityTooLarge

db = SQLAlchemy()
migrate = Migrate()


def create_app(config_name=None):
    app = Flask(__name__, instance_relative_config=True)

    if config_name == "testing":
        app.config.from_object("app.config.TestingConfig")
    else:
        app.config.from_object("app.config.Config")

    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config.get("UPLOAD_FOLDER", "uploads"), exist_ok=True)
    os.makedirs(app.config.get("EXPORT_FOLDER", "exports"), exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)

    from app.routes.dashboard import dashboard_bp
    from app.routes.project import project_bp
    from app.routes.editor import editor_bp
    from app.routes.api import api_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(project_bp, url_prefix="/project")
    app.register_blueprint(editor_bp, url_prefix="/editor")
    app.register_blueprint(api_bp, url_prefix="/api")

    @app.errorhandler(RequestEntityTooLarge)
    def handle_file_too_large(e):
        max_mb = app.config.get("MAX_CONTENT_LENGTH", 0) // (1024 * 1024)
        flash(f"Upload too large. Maximum total size is {max_mb} MB.", "error")
        return redirect(url_for("project.new"))

    with app.app_context():
        db.create_all()

    return app
