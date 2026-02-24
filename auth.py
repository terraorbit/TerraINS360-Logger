"""
TerraINS360 - Authentication & RBAC Module
============================================
JWT-based auth with SuperAdmin (1) + Users (N).
Passwords hashed with bcrypt. Tokens in HTTP cookies.
"""

import json
import time
import threading
import bcrypt
import jwt
from pathlib import Path
from datetime import datetime

AUTH_DIR = Path(__file__).parent / "data"
USERS_FILE = AUTH_DIR / "users.json"
JWT_SECRET = "TerraINS360_v3_secret_key_2026"
JWT_ALGO = "HS256"
TOKEN_EXPIRY = 86400 * 7  # 7 days


class AuthManager:
    """Manages users, passwords, roles, JWT tokens."""

    def __init__(self):
        self._users = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        AUTH_DIR.mkdir(parents=True, exist_ok=True)
        if USERS_FILE.exists():
            try:
                with open(USERS_FILE, "r") as f:
                    self._users = json.load(f)
            except Exception:
                self._users = {}

        # Ensure at least one superadmin exists
        has_admin = any(u.get("role") == "superadmin" for u in self._users.values())
        if not has_admin:
            self._create_default_admin()

    def _save(self):
        AUTH_DIR.mkdir(parents=True, exist_ok=True)
        with open(USERS_FILE, "w") as f:
            json.dump(self._users, f, indent=2)

    def _create_default_admin(self):
        """Create default superadmin account."""
        pwd_hash = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt()).decode()
        self._users["admin"] = {
            "username": "admin",
            "password_hash": pwd_hash,
            "role": "superadmin",
            "display_name": "Super Admin",
            "email": "",
            "created": datetime.now().isoformat(),
            "last_login": "",
            "active": True,
            "permissions": ["all"],
        }
        self._save()

    def _find_user_key(self, username: str) -> str | None:
        """Case-insensitive username lookup. Returns actual stored key."""
        # Try exact match first
        if username in self._users:
            return username
        # Case-insensitive fallback
        lower = username.lower()
        for key in self._users:
            if key.lower() == lower:
                return key
        return None

    def authenticate(self, username: str, password: str) -> dict | None:
        """Verify credentials, return user dict or None."""
        with self._lock:
            real_key = self._find_user_key(username)
            if not real_key:
                return None
            user = self._users[real_key]
            if not user.get("active", True):
                return None
            try:
                if bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
                    user["last_login"] = datetime.now().isoformat()
                    self._save()
                    return {k: v for k, v in user.items() if k != "password_hash"}
                return None
            except Exception:
                return None

    def create_token(self, username: str, role: str) -> str:
        """Generate JWT token."""
        payload = {
            "sub": username,
            "role": role,
            "iat": int(time.time()),
            "exp": int(time.time()) + TOKEN_EXPIRY,
        }
        return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

    def verify_token(self, token: str) -> dict | None:
        """Verify JWT, return payload or None."""
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
            username = payload.get("sub")
            if username and self._find_user_key(username):
                return payload
            return None
        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

    def get_user_from_token(self, token: str) -> dict | None:
        """Get full user info from JWT token."""
        payload = self.verify_token(token)
        if not payload:
            return None
        with self._lock:
            real_key = self._find_user_key(payload["sub"])
            if real_key:
                user = self._users[real_key]
                return {k: v for k, v in user.items() if k != "password_hash"}
            return None

    # ── User Management (SuperAdmin only) ──

    def create_user(self, username: str, password: str, display_name: str = "",
                    email: str = "", role: str = "user") -> dict:
        """Create a new user. Only 1 superadmin allowed."""
        with self._lock:
            if username in self._users:
                return {"error": "Username already exists"}

            if role == "superadmin":
                has_admin = any(u.get("role") == "superadmin" for u in self._users.values())
                if has_admin:
                    return {"error": "Only one superadmin allowed"}

            pwd_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
            self._users[username] = {
                "username": username,
                "password_hash": pwd_hash,
                "role": role,
                "display_name": display_name or username,
                "email": email,
                "created": datetime.now().isoformat(),
                "last_login": "",
                "active": True,
                "permissions": self._default_permissions(role),
            }
            self._save()
            return {"status": "created", "username": username}

    def update_user(self, username: str, **kwargs) -> dict:
        """Update user fields (not password)."""
        with self._lock:
            user = self._users.get(username)
            if not user:
                return {"error": "User not found"}
            for k, v in kwargs.items():
                if k not in ("password_hash", "username") and k in user:
                    user[k] = v
            self._save()
            return {"status": "updated"}

    def change_password(self, username: str, new_password: str) -> dict:
        """Change a user's password."""
        with self._lock:
            user = self._users.get(username)
            if not user:
                return {"error": "User not found"}
            user["password_hash"] = bcrypt.hashpw(
                new_password.encode(), bcrypt.gensalt()
            ).decode()
            self._save()
            return {"status": "password_changed"}

    def delete_user(self, username: str) -> dict:
        """Delete user. Cannot delete the superadmin."""
        with self._lock:
            user = self._users.get(username)
            if not user:
                return {"error": "User not found"}
            if user["role"] == "superadmin":
                return {"error": "Cannot delete superadmin"}
            del self._users[username]
            self._save()
            return {"status": "deleted"}

    def toggle_user(self, username: str, active: bool) -> dict:
        """Enable/disable a user."""
        with self._lock:
            user = self._users.get(username)
            if not user:
                return {"error": "User not found"}
            if user["role"] == "superadmin":
                return {"error": "Cannot disable superadmin"}
            user["active"] = active
            self._save()
            return {"status": "toggled", "active": active}

    def list_users(self) -> list:
        """List all users (without password hashes)."""
        with self._lock:
            return [
                {k: v for k, v in u.items() if k != "password_hash"}
                for u in self._users.values()
            ]

    def _default_permissions(self, role: str) -> list:
        if role == "superadmin":
            return ["all"]
        return ["survey", "tracks", "gnss"]


auth_manager = AuthManager()
