# -*- coding: utf-8 -*-
from __future__ import print_function

""" Module for running planner portfolios.

Memory limits: We apply the same memory limit that is given to the
plan script to each planner call. Note that this setup does not work if
the sum of the memory usage of the Python process and the planner calls
is limited. In this case the Python process might get killed although
we would like to kill only the single planner call and continue with
the remaining configurations. If we ever want to support this scenario
we will have to reduce the memory limit of the planner calls by the
amount of memory that the Python process needs. On maia for example
this amounts to 128MB of reserved virtual memory. We can make Python
reserve less space by lowering the soft limit for virtual memory before
the process is started.
"""

__all__ = ["run"]

# TODO: Rename portfolio.py to portfolio_runner.py

import itertools
import math
import os
import re
import resource
import signal
import subprocess
import sys


DEFAULT_TIMEOUT = 1800

# Exit codes.
EXIT_PLAN_FOUND = 0
EXIT_CRITICAL_ERROR = 1
EXIT_INPUT_ERROR = 2
EXIT_UNSUPPORTED = 3
EXIT_UNSOLVABLE = 4
EXIT_UNSOLVED_INCOMPLETE = 5
EXIT_OUT_OF_MEMORY = 6
EXIT_TIMEOUT = 7
EXIT_TIMEOUT_AND_MEMORY = 8
EXIT_SIGXCPU = -signal.SIGXCPU

EXPECTED_EXITCODES = set([
    EXIT_PLAN_FOUND, EXIT_UNSOLVABLE, EXIT_UNSOLVED_INCOMPLETE,
    EXIT_OUT_OF_MEMORY, EXIT_TIMEOUT])

# The portfolio's exitcode is determined as follows:
# There is exactly one type of unexpected exit code -> use it.
# There are multiple types of unexpected exit codes -> EXIT_CRITICAL_ERROR.
# [..., EXIT_PLAN_FOUND, ...] -> EXIT_PLAN_FOUND
# [..., EXIT_UNSOLVABLE, ...] -> EXIT_UNSOLVABLE
# [..., EXIT_UNSOLVED_INCOMPLETE, ...] -> EXIT_UNSOLVED_INCOMPLETE
# [..., EXIT_OUT_OF_MEMORY, ..., EXIT_TIMEOUT, ...] -> EXIT_TIMEOUT_AND_MEMORY
# [..., EXIT_TIMEOUT, ...] -> EXIT_TIMEOUT
# [..., EXIT_OUT_OF_MEMORY, ...] -> EXIT_OUT_OF_MEMORY


def set_limit(kind, soft, hard):
    try:
        resource.setrlimit(kind, (soft, hard))
    except (OSError, ValueError), err:
        # This can happen if the limit has already been set externally.
        print("Limit for %s could not be set to %s (%s). Previous limit: %s" %
              (kind, (soft, hard), err, resource.getrlimit(kind)), file=sys.stderr)


def get_plan_cost_and_cost_type(plan_file):
    with open(plan_file) as input_file:
        for line in input_file:
            match = re.match(r"; cost = (\d+) \((unit-cost|general-cost)\)\n", line)
            if match:
                return int(match.group(1)), match.group(2)
    os.remove(plan_file)
    print("Could not retrieve plan cost from %s. Deleted the file." % plan_file)
    return None, None


def get_plan_file(plan_prefix, number):
    return "%s.%d" % (plan_prefix, number)


def get_plan_files(plan_prefix):
    plan_files = []
    for index in itertools.count(start=1):
        plan_file = get_plan_file(plan_prefix, index)
        if os.path.exists(plan_file):
            plan_files.append(plan_file)
        else:
            break
    return plan_files


def get_cost_type(plan_prefix):
    for plan_file in get_plan_files(plan_prefix):
        _, cost_type = get_plan_cost_and_cost_type(plan_file)
        if cost_type is not None:
            return cost_type


def get_g_bound_and_number_of_plans(plan_prefix):
    plan_costs = []
    for plan_file in get_plan_files(plan_prefix):
        plan_cost, _ = get_plan_cost_and_cost_type(plan_file)
        if plan_cost is not None:
            if plan_costs and not plan_costs[-1] > plan_cost:
                raise SystemExit(
                    "Plan costs must decrease: %s" %
                    " -> ".join(str(c) for c in plan_costs + [plan_cost]))
            plan_costs.append(plan_cost)
    bound = min(plan_costs) if plan_costs else "infinity"
    return bound, len(plan_costs)


def adapt_search(args, search_cost_type, heuristic_cost_type, plan_prefix):
    g_bound, plan_no = get_g_bound_and_number_of_plans(plan_prefix)
    for index, arg in enumerate(args):
        if arg == "--heuristic":
            heuristic = args[index + 1]
            heuristic = heuristic.replace("H_COST_TYPE", heuristic_cost_type)
            args[index + 1] = heuristic
        elif arg == "--search":
            search = args[index + 1]
            if search.startswith("iterated"):
                if "plan_counter=PLANCOUNTER" not in search:
                    raise ValueError("When using iterated search, we must add "
                                     "the option plan_counter=PLANCOUNTER")
                plan_file = plan_prefix
            else:
                plan_file = get_plan_file(plan_prefix, plan_no + 1)
            for name, value in [
                    ("BOUND", g_bound),
                    ("PLANCOUNTER", plan_no),
                    ("H_COST_TYPE", heuristic_cost_type),
                    ("S_COST_TYPE", search_cost_type)]:
                search = search.replace(name, str(value))
            args[index + 1] = search
            break
    print("g bound: %s" % g_bound)
    print("next plan number: %d" % (plan_no + 1))
    return plan_file


def run_search(executable, args, sas_file, plan_file, timeout=None, memory=None):
    complete_args = [executable] + args + ["--plan-file", plan_file]
    print("args: %s" % complete_args)
    sys.stdout.flush()

    def set_limits():
        if timeout is not None:
            # Don't try to raise the hard limit.
            _, external_hard_limit = resource.getrlimit(resource.RLIMIT_CPU)
            if external_hard_limit == resource.RLIM_INFINITY:
                external_hard_limit = float("inf")
            # Soft limit reached --> SIGXCPU.
            # Hard limit reached --> SIGKILL.
            soft_limit = int(math.ceil(timeout))
            hard_limit = min(soft_limit + 1, external_hard_limit)
            print("timeout: %.2f -> (%d, %d)" % (timeout, soft_limit, hard_limit))
            sys.stdout.flush()
            set_limit(resource.RLIMIT_CPU, soft_limit, hard_limit)
        if memory is not None:
            # Memory in Bytes
            set_limit(resource.RLIMIT_AS, memory, memory)
        else:
            set_limit(resource.RLIMIT_AS, -1, -1)

    with open(sas_file) as input_file:
        returncode = subprocess.call(complete_args, stdin=input_file,
                                     preexec_fn=set_limits)
    print("returncode: %d" % returncode)
    print()
    return returncode


def get_elapsed_time():
    ## Note: According to the os.times documentation, Windows sets the
    ## child time components to 0, so this won't work properly under
    ## Windows.
    ##
    ## TODO: Find a solution for this. A simple solution might be to
    ## just document this as a limitation under Windows that causes
    ## time slices for portfolios to be allocated slightly wrongly.
    ## Another solution would be to base time slices on wall-clock
    ## time under Windows.
    return sum(os.times()[:4])


def determine_timeout(remaining_time_at_start, configs, pos):
    remaining_time = remaining_time_at_start - get_elapsed_time()
    relative_time = configs[pos][0]
    print("remaining time: %s" % remaining_time)
    remaining_relative_time = sum(config[0] for config in configs[pos:])
    print("config %d: relative time %d, remaining %d" %
          (pos, relative_time, remaining_relative_time))
    # For the last config we have relative_time == remaining_relative_time, so
    # we use all of the remaining time at the end.
    run_timeout = remaining_time * relative_time / remaining_relative_time
    return run_timeout


def run_sat_config(configs, pos, search_cost_type, heuristic_cost_type,
                   executable, sas_file, plan_prefix, remaining_time_at_start,
                   memory):
    args = list(configs[pos][1])
    plan_file = adapt_search(args, search_cost_type, heuristic_cost_type,
                             plan_prefix)
    run_timeout = determine_timeout(remaining_time_at_start, configs, pos)
    if run_timeout <= 0:
        return None
    return run_search(executable, args, sas_file, plan_file, run_timeout, memory)


def run_sat(configs, executable, sas_file, plan_prefix, final_config,
            final_config_builder, remaining_time_at_start, memory):
    exitcodes = []
    # If the configuration contains S_COST_TYPE or H_COST_TYPE and the task
    # has non-unit costs, we start by treating all costs as one. When we find
    # a solution, we rerun the successful config with real costs.
    heuristic_cost_type = "one"
    search_cost_type = "one"
    changed_cost_types = False
    while configs:
        configs_next_round = []
        for pos, (relative_time, args) in enumerate(configs):
            args = list(args)
            exitcode = run_sat_config(
                configs, pos, search_cost_type, heuristic_cost_type,
                executable, sas_file, plan_prefix, remaining_time_at_start,
                memory)
            if exitcode is None:
                return exitcodes

            exitcodes.append(exitcode)
            if exitcode == EXIT_UNSOLVABLE:
                return exitcodes

            if exitcode == EXIT_PLAN_FOUND:
                configs_next_round.append(configs[pos][:])
                if (not changed_cost_types and can_change_cost_type(args) and
                        get_cost_type(plan_prefix) == "general-cost"):
                    print("Switch to real costs and repeat last run.")
                    changed_cost_types = True
                    search_cost_type = "normal"
                    heuristic_cost_type = "plusone"
                    exitcode = run_sat_config(
                        configs, pos, search_cost_type, heuristic_cost_type,
                        executable, sas_file, plan_prefix,
                        remaining_time_at_start, memory)
                    if exitcode is None:
                        return exitcodes

                    exitcodes.append(exitcode)
                    if exitcode == EXIT_UNSOLVABLE:
                        return exitcodes
                if final_config_builder:
                    print("Build final config.")
                    final_config = final_config_builder(args[:])
                    break

        if final_config:
            break

        # Only run the successful configs in the next round.
        configs = configs_next_round

    if final_config:
        print("Abort portfolio and run final config.")
        exitcode = run_sat_config(
            [(1, list(final_config))], 0, search_cost_type,
            heuristic_cost_type, executable, sas_file, plan_prefix,
            remaining_time_at_start, memory)
        if exitcode is not None:
            exitcodes.append(exitcode)
    return exitcodes


def run_opt(configs, executable, sas_file, plan_prefix, remaining_time_at_start,
            memory):
    exitcodes = []
    for pos, (relative_time, args) in enumerate(configs):
        timeout = determine_timeout(remaining_time_at_start, configs, pos)
        exitcode = run_search(executable, args, sas_file, plan_prefix, timeout, memory)
        exitcodes.append(exitcode)

        if exitcode in [EXIT_PLAN_FOUND, EXIT_UNSOLVABLE]:
            break
    return exitcodes


def generate_exitcode(exitcodes):
    print("Exit codes: %s" % exitcodes)
    exitcodes = set(exitcodes)
    if EXIT_SIGXCPU in exitcodes:
        exitcodes.remove(EXIT_SIGXCPU)
        exitcodes.add(EXIT_TIMEOUT)
    unexpected_codes = exitcodes - EXPECTED_EXITCODES
    if unexpected_codes:
        print("Error: Unexpected exit codes: %s" % list(unexpected_codes))
        if len(unexpected_codes) == 1:
            return unexpected_codes.pop()
        else:
            return EXIT_CRITICAL_ERROR
    for code in [EXIT_PLAN_FOUND, EXIT_UNSOLVABLE, EXIT_UNSOLVED_INCOMPLETE]:
        if code in exitcodes:
            return code
    for code in [EXIT_OUT_OF_MEMORY, EXIT_TIMEOUT]:
        if exitcodes == set([code]):
            return code
    if exitcodes == set([EXIT_OUT_OF_MEMORY, EXIT_TIMEOUT]):
        return EXIT_TIMEOUT_AND_MEMORY
    print("Error: Unhandled exit codes: %s" % exitcodes)
    return EXIT_CRITICAL_ERROR


def can_change_cost_type(args):
    return any('S_COST_TYPE' in part or 'H_COST_TYPE' in part for part in args)


def get_portfolio_attributes(portfolio):
    attributes = {}
    try:
        execfile(portfolio, attributes)
    except ImportError as err:
        if str(err) == "No module named portfolio":
            raise ValueError(
                "The portfolio format has changed. New portfolios may only "
                "define attributes. See the FDSS portfolios for examples.")
        else:
            raise
    if "CONFIGS" not in attributes:
        raise ValueError("portfolios must define CONFIGS")
    if "OPTIMAL" not in attributes:
        raise ValueError("portfolios must define OPTIMAL")
    return attributes


def run(portfolio, executable, sas_file, plan_prefix):
    attributes = get_portfolio_attributes(portfolio)
    configs = attributes["CONFIGS"]
    optimal = attributes["OPTIMAL"]
    final_config = attributes.get("FINAL_CONFIG")
    final_config_builder = attributes.get("FINAL_CONFIG_BUILDER")
    timeout = attributes.get("TIMEOUT")

    # Time limits are either positive values in seconds or -1 (unlimited).
    soft_time_limit, hard_time_limit = resource.getrlimit(resource.RLIMIT_CPU)
    print("External time limits: %d, %d" % (soft_time_limit, hard_time_limit))
    external_time_limit = None
    if soft_time_limit != resource.RLIM_INFINITY:
        external_time_limit = soft_time_limit
    elif hard_time_limit != resource.RLIM_INFINITY:
        external_time_limit = hard_time_limit
    if (external_time_limit is not None and
            timeout is not None and
            timeout != external_time_limit):
        print("The externally set timeout (%d) differs from the one "
              "in the portfolio file (%d). Is this expected?" %
              (external_time_limit, timeout), file=sys.stderr)
    # Prefer limits in the order: external soft limit, external hard limit,
    # from portfolio file, default.
    if external_time_limit is not None:
        timeout = external_time_limit
    elif timeout is None:
        print("No timeout has been set for the portfolio so we take "
              "the default of %ds." % DEFAULT_TIMEOUT, file=sys.stderr)
        timeout = DEFAULT_TIMEOUT
    print("Internal time limit: %d" % timeout)

    # Memory limits are either positive values in Bytes or -1 (unlimited).
    soft_mem_limit, hard_mem_limit = resource.getrlimit(resource.RLIMIT_AS)
    print("External memory limits: %d, %d" % (soft_mem_limit, hard_mem_limit))
    if hard_mem_limit == resource.RLIM_INFINITY:
        memory = None
    else:
        memory = hard_mem_limit
    print("Internal memory limit: %s" % memory)

    remaining_time_at_start = float(timeout) - get_elapsed_time()
    print("remaining time at start: %.2f" % remaining_time_at_start)

    if optimal:
        exitcodes = run_opt(configs, executable, sas_file, plan_prefix,
                            remaining_time_at_start, memory)
    else:
        exitcodes = run_sat(configs, executable, sas_file, plan_prefix,
                            final_config, final_config_builder,
                            remaining_time_at_start, memory)
    exitcode = generate_exitcode(exitcodes)
    if exitcode != 0:
        raise subprocess.CalledProcessError(exitcode, ["run-portfolio", portfolio])
