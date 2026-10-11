"""
Flask application factory for C3-Mass Dashboard.
Uses Jinja2 templates for server-rendered pages.
"""
import os
from flask import Flask, jsonify, render_template
from .database import init_db
from . import models

def create_app():
    app = Flask(
        __name__,
        static_folder='../static',
        static_url_path='/static',
        template_folder='../dashboard',
    )

    init_db(app)

    # Register blueprints
    from .routes_rockets import bp as rockets_bp
    from .routes_data import bp as data_bp
    from .routes_engines import bp as engines_bp
    from .routes_missions import bp as missions_bp
    from .routes_import import bp as import_bp
    from .routes_export import bp as export_bp
    from .routes_query import bp as query_bp
    from .routes_compare import bp as compare_bp
    from .routes_folders import bp as folders_bp

    app.register_blueprint(folders_bp)
    app.register_blueprint(rockets_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(engines_bp)
    app.register_blueprint(missions_bp)
    app.register_blueprint(import_bp)
    app.register_blueprint(export_bp)
    app.register_blueprint(query_bp)
    app.register_blueprint(compare_bp)

    # Serve CSS/JS from dashboard directory
    @app.route('/')
    def workspace_home():
        return modern_workspace()

    @app.route('/workspace/')
    @app.route('/workspace/<path:filename>')
    def modern_workspace(filename='index.html'):
        """Component frontend, independent of the preserved legacy dashboard."""
        from flask import send_from_directory
        return send_from_directory(
            os.path.join(app.root_path, '..', 'frontend', 'dist'), filename,
            max_age=0 if filename == 'index.html' else 3600)

    @app.route('/css/<path:filename>')
    def css_files(filename):
        from flask import send_from_directory
        return send_from_directory(
            os.path.join(app.root_path, '..', 'dashboard', 'css'),
            filename,
            max_age=0,
        )

    @app.route('/js/<path:filename>')
    def js_files(filename):
        from flask import send_from_directory
        return send_from_directory(
            os.path.join(app.root_path, '..', 'dashboard', 'js'),
            filename,
            max_age=0,
        )

    @app.route('/legacy/')
    def index():
        """Render dashboard with all data pre-loaded."""
        import json
        rockets = models.list_rockets()
        engines = models.list_engines()
        missions = models.list_standard_missions()
        folders = models.get_folder_tree()
        return render_template('index.html',
            rockets_json=rockets,
            engines_json=engines,
            missions_json=missions,
            folders_json=folders,
        )

    @app.errorhandler(404)
    def not_found(e):
        return jsonify(error='Not found'), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify(error='Internal server error'), 500

    return app
