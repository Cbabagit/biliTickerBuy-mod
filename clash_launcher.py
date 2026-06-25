"""
Clash 子实例启动/停止脚本
=========================
用法:  python clash_launcher.py start [count=3]
       python clash_launcher.py stop
       python clash_launcher.py status
       python clash_launcher.py reconfigure [count=3]
"""

import os
import sys

# Add project root
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from util.proxy.ClashInstanceManager import (  # noqa: E402
    auto_start, stop_all, get_instance_statuses, get_assignments,
    add_instance, remove_instance,
    write_child_config,
)


def cmd_start(args):
    count = int(args[0]) if args else 5
    print(f"Auto-starting {count} Clash instances (node assignment + start)...")
    auto_start(count)
    statuses = get_instance_statuses()
    assignments = get_assignments()
    for s in statuses:
        iid = s["id"]
        srv = "ALIVE" if s["alive"] else "DEAD"
        node = assignments.get(iid, "")
        print(f"  Instance {iid}: {srv} | proxy=:{s['proxy_port']} api=:{s['api_port']} node={node}")
    return 0


def cmd_stop(args):
    print("Stopping all Clash instances...")
    stop_all()
    print("Done.")
    return 0


def cmd_status(args):
    statuses = get_instance_statuses()
    assignments = get_assignments()
    print(f"Instance count: {len(statuses)}")
    for s in statuses:
        iid = s["id"]
        if iid == 0:
            continue  # 主实例不显示在子实例列表
        srv = "ALIVE" if s["alive"] else "DEAD"
        assigned = assignments.get(iid, "")
        cur = s.get("current_node", "")
        node_str = f"assigned={assigned}" if assigned == cur else f"assigned={assigned} current={cur}"
        print(f"  Instance {iid}: {srv} | proxy=:{s['proxy_port']} api=:{s['api_port']} {node_str}")
    return 0


def cmd_reconfigure(args):
    count = int(args[0]) if args else 5
    print(f"Reconfiguring {count} Clash instances with latency-ranked assignments...")
    from util.proxy.ClashInstanceManager import _assign_nodes
    assignments = _assign_nodes(count)
    for instance_id, node in assignments.items():
        write_child_config(instance_id, assigned_node=node)
        print(f"  Instance {instance_id}: config with assigned node {node}")
    print("Done. (No restart performed; run 'stop' then 'start' to apply)")
    return 0


def cmd_auto(args):
    """auto = start + latency-ranked assignment"""
    return cmd_start(args)


def cmd_add(args):
    iid = add_instance()
    print(f"Added instance {iid}")
    return 0


def cmd_remove(args):
    iid = int(args[0]) if args else 1
    remove_instance(iid)
    print(f"Removed instance {iid}")
    return 0


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1

    cmd = sys.argv[1]
    args = sys.argv[2:]

    commands = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "reconfigure": cmd_reconfigure,
        "auto": cmd_auto,
        "add": cmd_add,
        "remove": cmd_remove,
    }

    if cmd not in commands:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        return 1

    return commands[cmd](args)


if __name__ == "__main__":
    sys.exit(main())
