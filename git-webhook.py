from flask import Flask, request
import subprocess
import logging
import requests
import ipaddress

# Set Containers to run pull on
containers = {
    "34bee3f5-fb2b-4bab-b45e-c303b1d15137": "main",
    "fbb6360b-1f8f-4768-a39e-340daf0eac6f": "dev",
    "51c6374c-c9ff-49bb-90b8-c68d1326fabe": "dev",
}

submodules = {
    "resources/[VL_Scripts]/[Cars]": "main",
    "resources/[VL_Scripts]/[Kleidung]": "main",
    "resources/[VL_Scripts]/[MLO]": "main",
}

# Set up logging to file
logging.basicConfig(filename='/home/github/webhook.log', level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')

app = Flask(__name__)

# New helper function to run docker commands.
def run_docker_command(container, *args):
    cmd = ["docker", "exec", container] + list(args)
    return subprocess.run(cmd, capture_output=True, text=True)

def status(container, path):
    result_status = run_docker_command(container, "git", "-C", path, "status", "--porcelain", "-uno")
    return result_status

def commit(container, path):
    result_commit = run_docker_command(container, "git", "-C", path, "commit", "-am", "Auto-commit by webhook")
    return result_commit

def pull(container, path, branch):
    result_pull = run_docker_command(container, "git", "-C", path, "pull", "origin", branch)
    if result_pull.returncode != 0:
        logging.error(f"Error during git pull in container {container}: {result_pull.stderr}")
        return "Error during Pull/Update", 500
    return result_pull

def push(container, path, branch):
    result_push = run_docker_command(container, "git", "-C", path, "push", "origin", branch)
    if result_push.returncode != 0:
        logging.error(f"Error during git push in container {container}: {result_push.stderr}")
        return "Error on Push", 500
    return result_push

def push_submodule(container, path, branch):
    run_docker_command(container, "git", "-C", path, "checkout", branch)
    result_push = run_docker_command(container, "git", "-C", path, "merge", "HEAD@{1}", branch)
    result_push = run_docker_command(container, "git", "-C", path, "push", "origin", branch)
    if result_push.returncode != 0:
        logging.error(f"Error during git push in container {container}: {result_push.stderr}")
        return "Error on Push", 500
    return result_push

def reset(container):
    result_reset = run_docker_command(container, "git", "-C", "/home/container/server-data", "reset", "--hard", "HEAD")
    if result_reset.returncode != 0:
        logging.error(f"Error during git reset in container {container}: {result_reset.stderr}")
        return "Error on Reset", 500
    return result_reset

def submodule_update(container):
    result_submodule = run_docker_command(container, "git", "-C", "/home/container/server-data", "submodule", "update", "--init", "--recursive", "--remote", "--force")
    if result_submodule.returncode != 0:
        logging.error(f"Error during git submodule update in container {container}: {result_submodule.stderr}")
        return "Error on Submodule Update", 500
    return result_submodule

# New function: update submodules using the commit specified in the repo's .submodules
def submodule_update_fixed(container):
    result = run_docker_command(container, "git", "-C", "/home/container/server-data", "submodule", "update", "--init", "--recursive")
    if result.returncode != 0:
         logging.error(f"Error during fixed git submodule update in container {container}: {result.stderr}")
         return "Error on Submodule Update (fixed)", 500
    return result

@app.route("/webhook", methods=["POST"])
def webhook():

    # Check if request is from GitHub.
    try:
        meta = requests.get("https://api.github.com/meta").json()
        valid_ips = meta.get("hooks", [])
    except Exception as e:
        logging.error("Failed to retrieve GitHub hooks IPs: %s", e)
        return "Error retrieving GitHub meta information", 500

    remote_ip = ipaddress.ip_address(request.remote_addr)
    if not any(remote_ip in ipaddress.ip_network(ip_range) for ip_range in valid_ips):
        logging.error("Unauthorized request from IP: %s", request.remote_addr)
        return "Unauthorized IP", 403
    
    # Check if github request is a push event.
    if request.headers.get("X-GitHub-Event") != "push":
        logging.info("Received not a push event")
        return "Not push Event", 500
    
    # Check if commit message contains "Auto-commit by webhook"
    payload = request.json
    if "Auto-commit by webhook" in payload["head_commit"]["message"]:
        logging.info("Auto-commit by webhook detected")
        return "Auto-commit by webhook", 202
    
    for container, branch in containers.items():
        if container == "fbb6360b-1f8f-4768-a39e-340daf0eac6f":
            # Auto commit, pull and push for submodules
            for path, branch_sub in submodules.items():
                status_out_sub = status(container, f"/home/container/server-data/{path}")
                if not status_out_sub.stdout.strip():
                    logging.info(f"{container}: No changes in {path}, only pulling")
                    pull(container, f"/home/container/server-data/{path}", branch_sub)
                    logging.info(f"{container}: Auto pull {path} successful in container")
                    continue
                commit(container, f"/home/container/server-data/{path}")
                logging.info(f"{container}: Auto commit {path} successful in container")
                pull(container, f"/home/container/server-data/{path}", branch_sub)
                logging.info(f"{container}: Auto pull {path} successful in container")
                push_submodule(container, f"/home/container/server-data/{path}", branch_sub)
                logging.info(f"{container}: Auto push {path} successful in container")
            # Use newest commits in submodules
            res_sub = submodule_update(container)
            if isinstance(res_sub, tuple):  # an error was returned
                return res_sub
            
            commit(container, "/home/container/server-data")
            logging.info(f"{container}: Auto commit successful in container")
            pull(container, "/home/container/server-data", branch)
            logging.info(f"{container}: Auto pull successful in container")
            push(container, "/home/container/server-data", branch)
            logging.info(f"{container}: Auto push successful in container")
        elif container == "34bee3f5-fb2b-4bab-b45e-c303b1d15137":
            status_out = status(container, "/home/container/server-data")
            if status_out.stdout.strip():
                reset(container)
                logging.info(f"{container}: Reset successful in container")
            pull(container, "/home/container/server-data", branch)
            logging.info(f"{container}: Git pull successful in container")
            res_sub = submodule_update_fixed(container)
            if isinstance(res_sub, tuple):  # an error was returned
                return res_sub
            logging.info(f"{container}: Submodules updated using specified branch in container")
        elif container == "51c6374c-c9ff-49bb-90b8-c68d1326fabe":
            status_out = status(container, "/home/container/server-data")
            if status_out.stdout.strip():
                reset(container)
                logging.info(f"{container}: Reset successful in container")
            pull(container, "/home/container/server-data", branch)
            logging.info(f"{container}: Git pull successful in container")
            res_sub = submodule_update_fixed(container)
            if isinstance(res_sub, tuple):  # an error was returned
                return res_sub
            logging.info(f"{container}: Submodules updated using specified branch in container")
    logging.info("Operations successful for all containers")
    return "OK", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
