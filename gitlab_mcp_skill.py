import os
import sys
import json
import argparse
import subprocess
import gitlab

GITLAB_URL = os.getenv("GITLAB_URL", "https://company.com")
PRIVATE_TOKEN = os.getenv("GITLAB_TOKEN")
PROJECT_ID = os.getenv("GITLAB_PROJECT_ID")
VERIFY_SSL = os.getenv("GITLAB_VERIFY_SSL", "true").lower() in ("true", "1", "yes")

if not PRIVATE_TOKEN or not PROJECT_ID:
    print(json.dumps({"error": "Missing env variables: GITLAB_TOKEN or GITLAB_PROJECT_ID"}), file=sys.stderr)
    sys.exit(1)

gl = gitlab.Gitlab(url=GITLAB_URL, private_token=PRIVATE_TOKEN, ssl_verify=VERIFY_SSL)
try:
    project = gl.projects.get(PROJECT_ID)
except Exception as e:
    print(json.dumps({"error": f"Connection to Self-managed GitLab failed: {str(e)}"}), file=sys.stderr)
    sys.exit(1)

def get_current_mr_iid():
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"]).decode("utf-8").strip()
        if branch in ["main", "master", "develop"]: return None
        mrs = project.mergerequests.list(source_branch=branch, state='opened', limit=1)
        if mrs: return mrs.iid
    except Exception: pass
    return None

def handle_read(mr_iid):
    mr = project.mergerequests.get(mr_iid)
    discussions = mr.discussions.list(iterator=True)
    result = []
    for disc in discussions:
        if disc.attributes['notes']['system']: continue
        notes = [{
            "note_id": n['id'],
            "author": n['author']['username'],
            "body": n['body'],
            "resolved": n.get('resolved', False)
        } for n in disc.attributes['notes']]
        result.append({"discussion_id": disc.id, "notes": notes, "resolved": disc.attributes.get('resolved', False)})
    print(json.dumps({"status": "success", "discussions": result}, ensure_ascii=False, indent=2))

def handle_diff(mr_iid):
    mr = project.mergerequests.get(mr_iid)
    changes = mr.changes()
    meta = {"base_sha": changes['diff_refs']['base_sha'], "start_sha": changes['diff_refs']['start_sha'], "head_sha": changes['diff_refs']['head_sha']}
    files = [{"old_path": f['old_path'], "new_path": f['new_path'], "diff": f['diff']} for f in changes['files']]
    print(json.dumps({"status": "success", "meta": meta, "files": files}, ensure_ascii=False, indent=2))

def handle_inline_comment(mr_iid, path, line, text):
    mr = project.mergerequests.get(mr_iid)
    changes = mr.changes()
    position = {
        'base_sha': changes['diff_refs']['base_sha'],
        'start_sha': changes['diff_refs']['start_sha'],
        'head_sha': changes['diff_refs']['head_sha'],
        'position_type': 'text',
        'new_path': path,
        'new_line': line
    }
    discussion = mr.discussions.create({'body': text, 'position': position})
    print(json.dumps({"status": "success", "discussion_id": discussion.id}))

def handle_resolve(mr_iid, discussion_id, status):
    mr = project.mergerequests.get(mr_iid)
    discussion = mr.discussions.get(discussion_id)
    discussion.resolved = (status.lower() == 'true')
    discussion.save()
    print(json.dumps({"status": "success", "discussion_id": discussion_id, "resolved": discussion.resolved}))

def handle_pipeline_logs(mr_iid):
    mr = project.mergerequests.get(mr_iid)
    pipelines = mr.pipelines()
    if not pipelines:
        print(json.dumps({"status": "success", "message": "No pipelines found"}))
        return
    
    latest_pipeline = project.pipelines.get(pipelines['id'])
    if latest_pipeline.status == 'success':
        print(json.dumps({"status": "success", "pipeline_status": "success"}))
        return

    failed_jobs_logs = []
    for job in latest_pipeline.jobs.list(scope='failed'):
        live_job = project.jobs.get(job.id)
        log = live_job.trace().decode('utf-8', errors='ignore')[-4000:]
        failed_jobs_logs.append({"job_name": job.name, "stage": job.stage, "trace_tail": log})
        
    print(json.dumps({"status": "failed", "pipeline_id": latest_pipeline.id, "failed_jobs": failed_jobs_logs}, ensure_ascii=False, indent=2))

def main():
    parser = argparse.ArgumentParser(description="GigaCode Advanced GitLab Bridge")
    parser.add_argument("action", choices=["read", "diff", "inline", "resolve", "pipeline"])
    parser.add_argument("--mr", type=int)
    parser.add_argument("--text", type=str)
    parser.add_argument("--path", type=str)
    parser.add_argument("--line", type=int)
    parser.add_argument("--discussion", type=str)
    parser.add_argument("--status", type=str, choices=["true", "false"])
    
    args = parser.parse_args()
    mr_iid = args.mr or get_current_mr_iid()
    if not mr_iid:
        print(json.dumps({"error": "Active MR not found. Pass --mr explicitly."}))
        sys.exit(1)

    try:
        if args.action == "read": handle_read(mr_iid)
        elif args.action == "diff": handle_diff(mr_iid)
        elif args.action == "inline":
            if not args.path or not args.line or not args.text: raise ValueError("Missing path, line or text")
            handle_inline_comment(mr_iid, args.path, args.line, args.text)
        elif args.action == "resolve":
            if not args.discussion or not args.status: raise ValueError("Missing discussion or status")
            handle_resolve(mr_iid, args.discussion, args.status)
        elif args.action == "pipeline": handle_pipeline_logs(mr_iid)
    except Exception as e:
        print(json.dumps({"status": "error", "message": str(e)}))
        sys.exit(1)

if __name__ == "__main__": main()
