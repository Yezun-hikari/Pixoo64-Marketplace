import os
import json
import logging
import requests
import subprocess
import tempfile
import shutil
import time
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class RepoManager:
    """Manages external GitHub repositories and local metadata caching."""
    
    def __init__(self, plugins_dir: str = "plugins", repos_file: str = "repositories.json"):
        self.plugins_dir = plugins_dir
        self.repos_file = repos_file
        self.repositories: Dict[str, dict] = {}
        self.load_repos()

    def load_repos(self) -> None:
        """Loads repository metadata from JSON cache."""
        if os.path.exists(self.repos_file):
            try:
                with open(self.repos_file, 'r', encoding='utf-8') as f:
                    self.repositories = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load {self.repos_file}: {e}", exc_info=True)
                self.repositories = {}

    def save_repos(self) -> None:
        """Safely saves repositories dictionary atomically using a temporary file."""
        try:
            fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(self.repos_file)), prefix="repos_", suffix=".json")
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(self.repositories, f, indent=4)
            
            # Atomic replace
            shutil.move(temp_path, self.repos_file)
        except Exception as e:
            logger.error(f"Failed to save {self.repos_file}: {e}", exc_info=True)

    def add_repository(self, url: str) -> dict:
        """Adds a new GitHub repository to the catalog."""
        url = url.rstrip('/')
        if url.endswith('.git'):
            url = url[:-4]
            
        repo_name = url.split('/')[-1]
        metadata = self.fetch_remote_metadata(url)
        
        self.repositories[url] = {
            "name": repo_name,
            "url": url,
            "metadata": metadata,
            "remote_hash": self.get_remote_hash(url)
        }
        self.save_repos()
        return self.repositories[url]

    def remove_repository(self, url: str) -> bool:
        """Removes a repository from the catalog."""
        if url in self.repositories:
            del self.repositories[url]
            self.save_repos()
            return True
        return False

    def get_all_repositories(self) -> Dict[str, dict]:
        return self.repositories

    def fetch_remote_metadata(self, url: str) -> dict:
        """Fetches plugin.json metadata from GitHub raw content directly."""
        parts = url.replace("https://github.com/", "").split('/')
        if len(parts) >= 2:
            owner, repo = parts[0], parts[1]
            for branch in ["main", "master"]:
                # Append timestamp to bypass aggressive GitHub raw cache
                raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/plugin.json?t={int(time.time())}"
                try:
                    r = requests.get(raw_url, timeout=10)
                    if r.status_code == 200:
                        return r.json()
                except requests.RequestException as e:
                    logger.warning(f"Failed to fetch metadata from {raw_url}: {e}")
                except json.JSONDecodeError as e:
                    logger.warning(f"Invalid JSON in metadata from {raw_url}: {e}")
        return {}

    def get_remote_hash(self, url: str) -> Optional[str]:
        """Fetches the latest commit hash from the remote repository."""
        try:
            output = subprocess.check_output(["git", "ls-remote", url, "HEAD"], text=True, stderr=subprocess.DEVNULL)
            if output:
                return output.split()[0]
        except subprocess.CalledProcessError:
            logger.warning(f"Failed to get remote hash for {url}")
        except Exception as e:
            logger.error(f"Unexpected error getting remote hash for {url}: {e}")
        return None

    def get_local_hash(self, repo_name: str) -> Optional[str]:
        """Fetches the currently installed commit hash of a local plugin."""
        target_dir = os.path.join(self.plugins_dir, repo_name)
        if os.path.exists(os.path.join(target_dir, ".git")):
            try:
                output = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target_dir, text=True, stderr=subprocess.DEVNULL)
                return output.strip()
            except Exception:
                logger.warning(f"Failed to get local git hash for {repo_name}")
        return None

    def force_scan(self) -> None:
        """Updates metadata and remote hashes for all tracked repositories."""
        for url, data in self.repositories.items():
            data["remote_hash"] = self.get_remote_hash(url)
            data["metadata"] = self.fetch_remote_metadata(url)
        self.save_repos()
