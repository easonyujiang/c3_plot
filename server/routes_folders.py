"""
Folder CRUD endpoints.
"""
import sqlite3
from flask import Blueprint, request, jsonify
from . import models

bp = Blueprint('folders', __name__)


@bp.route('/api/folders', methods=['GET'])
def get_tree():
    """Return full folder tree with rockets."""
    tree = models.get_folder_tree()
    flat = models.list_folders()
    return jsonify(tree=tree, flat=flat)


@bp.route('/api/folders', methods=['POST'])
def create():
    """Create a new folder. Body: {name, parent_id?}"""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400

    if not isinstance(data, dict):
        return jsonify(error='JSON body must be an object'), 400
    name = data.get('name', '').strip() if isinstance(data.get('name', ''), str) else ''
    if not name:
        return jsonify(error='Folder name required'), 400

    parent_id = data.get('parent_id')
    if parent_id is not None and (not isinstance(parent_id, int) or isinstance(parent_id, bool)):
        return jsonify(error='parent_id must be an integer or null'), 400
    fid = models.create_folder(name, parent_id)
    if fid is None:
        return jsonify(error='Folder already exists at this level'), 409

    return jsonify(id=fid, name=name, parent_id=parent_id), 201


@bp.route('/api/folders/<int:fid>', methods=['PUT'])
def rename(fid):
    """Rename a folder. Body: {name}"""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400

    if not isinstance(data, dict):
        return jsonify(error='JSON body must be an object'), 400
    name = data.get('name', '').strip() if isinstance(data.get('name', ''), str) else ''
    if not name:
        return jsonify(error='Name required'), 400

    try:
        if not models.rename_folder(fid, name):
            return jsonify(error='Not found'), 404
    except sqlite3.IntegrityError:
        models.get_db().rollback()
        return jsonify(error='Folder already exists at this level'), 409
    return jsonify(ok=True)


@bp.route('/api/folders/<int:fid>', methods=['DELETE'])
def delete(fid):
    """Delete a folder. Rockets inside move to root."""
    if not models.delete_folder(fid):
        return jsonify(error='Not found'), 404
    return jsonify(deleted=fid)


@bp.route('/api/rockets/<slug>/move', methods=['POST'])
def move_rocket(slug):
    """Move a rocket to a folder. Body: {folder_id: int|null}"""
    try:
        data = request.get_json(force=True)
    except Exception:
        return jsonify(error='Invalid JSON'), 400

    if not isinstance(data, dict):
        return jsonify(error='JSON body must be an object'), 400
    folder_id = data.get('folder_id')  # None = root
    if folder_id is not None and (not isinstance(folder_id, int) or isinstance(folder_id, bool)):
        return jsonify(error='folder_id must be an integer or null'), 400
    if not models.move_rocket(slug, folder_id):
        return jsonify(error='Rocket or folder not found'), 404
    return jsonify(slug=slug, folder_id=folder_id)
