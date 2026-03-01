import os
import yaml
import requests
from pathlib import Path
from typing import Optional

AUTH_HOST = "https://webapp-prod.cloud.remarkable.engineering"

class AuthManager:
    def __init__(self, config_path: Optional[str] = None):
        if config_path:
            self.config_path = Path(config_path)
        else:
            # Default rmapi paths
            xdg_config = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
            potential_paths = [
                os.environ.get("RMAPI_CONFIG"),
                os.path.expanduser("~/.rmapi"),
                os.path.join(xdg_config, "rmapi/rmapi.conf")
            ]
            # Use the first one that exists, or default to the XDG path
            final_path = None
            for p in potential_paths:
                if p and os.path.exists(p):
                    final_path = p
                    break
            
            self.config_path = Path(final_path if final_path else potential_paths[2])

        self.device_token = ""
        self.user_token = ""
        self.load_tokens()

    def load_tokens(self):
        if self.config_path.exists():
            with open(self.config_path, "r") as f:
                try:
                    data = yaml.safe_load(f)
                    self.device_token = data.get("devicetoken", "")
                    self.user_token = data.get("usertoken", "")
                except yaml.YAMLError:
                    pass

    def save_tokens(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "devicetoken": self.device_token,
            "usertoken": self.user_token
        }
        with open(self.config_path, "w") as f:
            yaml.safe_dump(data, f)

    def refresh_user_token(self):
        """Fetch a new User Token using the Device Token."""
        if not self.device_token:
            raise ValueError("Device Token is missing. Please register with rmapi first.")

        url = f"{AUTH_HOST}/token/json/2/user/new"
        headers = {"Authorization": f"Bearer {self.device_token}"}
        
        response = requests.post(url, headers=headers)
        if response.status_code == 200:
            self.user_token = response.text.strip()
            self.save_tokens()
            return self.user_token
        else:
            raise Exception(f"Failed to refresh User Token: {response.status_code} {response.text}")

    def get_user_token(self, force_refresh=False):
        if not self.user_token or force_refresh:
            return self.refresh_user_token()
        return self.user_token
