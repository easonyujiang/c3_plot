"""Flask API and static React workspace application."""
import os
from flask import Flask, jsonify
from .database import init_db

def create_app():
    app = Flask(
        __name__,
        static_folder='../static',
        static_url_path='/static',
        template_folder=None,
    )

    init_db(app)

    from .routes_system import bp as system_bp
    app.register_blueprint(system_bp)

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

    @app.route('/')
    def workspace_home():
        return modern_workspace()

    @app.route('/workspace/')
    @app.route('/workspace/<path:filename>')
    def modern_workspace(filename='index.html'):
        """Serve the built workspace and its assets."""
        from flask import send_from_directory
        return send_from_directory(
            os.path.join(app.root_path, '..', 'frontend', 'dist'), filename,
            max_age=0 if filename == 'index.html' else 3600)

    @app.route('/favicon.ico')
    def favicon():
        return app.send_static_file('favicon.svg')

    @app.errorhandler(404)
    def not_found(e):
        return jsonify(error='Not found'), 404

    @app.errorhandler(500)
    def server_error(e):
        return jsonify(error='Internal server error'), 500

    return app
