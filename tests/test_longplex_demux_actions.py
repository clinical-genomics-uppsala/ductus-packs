"""Structural tests for the two ductus.run_longplex_demux_* actions.

    python3 -m unittest tests.test_longplex_demux_actions -v

No st2: these read the YAML and assert on the wiring, which is where the
mistakes in an Orquesta action actually live. Three classes of them:

  - a parameter declared on the action but missing from the workflow's
    `input:` list is silently dropped, so the task runs with a default (or
    an empty string) and nothing says so;
  - `result().status_url` instead of `result().result.status_url`. The st2
    python runner wraps a returned dict in {stdout, stderr, exit_code,
    result}, so the shorter form reads nothing. The Miarka reheader workflow
    has this bug today (docs/deferred_findings.md item 8) and it is the file
    the Miarka variant here was written from;
  - --samples-info vs --sample-map. Both exist in this pack, mean different
    files, and are one word apart.
"""

import os
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTIONS = os.path.join(REPO_ROOT, "actions")

# Parameters every Orquesta action in this pack carries for st2's own use;
# they are deliberately not workflow inputs.
ST2_PARAMETERS = {"context", "workflow"}


def load(path):
    with open(path) as handle:
        return yaml.safe_load(handle)


class ActionContract(object):
    """The wiring assertions both actions must satisfy.

    Not a TestCase itself: subclasses mix it with unittest.TestCase, so the
    contract is not also collected and run once with no action to read.
    """

    action_name = None

    def setUp(self):
        self.action_path = os.path.join(ACTIONS, "%s.yaml" % self.action_name)
        self.action = load(self.action_path)
        self.workflow_path = os.path.join(ACTIONS, self.action["entry_point"])
        self.workflow = load(self.workflow_path)

    def workflow_text(self):
        with open(self.workflow_path) as handle:
            return handle.read()

    def test_action_is_registered_the_way_the_pack_expects(self):
        self.assertEqual(self.action["name"], self.action_name)
        self.assertEqual(self.action["pack"], "ductus")
        self.assertEqual(self.action["runner_type"], "orquesta")
        self.assertTrue(
            os.path.isfile(self.workflow_path),
            "entry_point %s does not exist" % self.action["entry_point"],
        )

    def test_workflow_parameter_is_the_action_itself(self):
        """A mismatch here makes st2 run a different workflow than the file."""
        self.assertEqual(
            self.action["parameters"]["workflow"]["default"],
            "ductus.%s" % self.action_name,
        )

    def test_every_workflow_input_is_an_action_parameter(self):
        declared = set(self.action["parameters"])
        for name in self.workflow["input"]:
            self.assertIn(
                name,
                declared,
                "workflow input %r is not a parameter of %s, so it can never "
                "be supplied" % (name, self.action_name),
            )

    def test_every_action_parameter_reaches_the_workflow(self):
        for name in self.action["parameters"]:
            if name in ST2_PARAMETERS:
                continue
            self.assertIn(
                name,
                self.workflow["input"],
                "parameter %r is declared on %s but absent from the "
                "workflow's input list, so setting it does nothing"
                % (name, self.action_name),
            )

    def test_failures_are_notified_and_then_fail_the_workflow(self):
        """Every pipeline action in this pack ends a failure the same way."""
        tasks = self.workflow["tasks"]
        self.assertIn("bioinfo_error_notifier", tasks)
        self.assertEqual(tasks["bioinfo_error_notifier"]["action"], "core.sendmail")
        transitions = yaml.dump(tasks["bioinfo_error_notifier"]["next"])
        self.assertIn("fail", transitions)

    def test_every_task_routes_its_failure_somewhere(self):
        for name, task in self.workflow["tasks"].items():
            if name == "bioinfo_error_notifier":
                continue
            transitions = yaml.dump(task.get("next") or [])
            self.assertIn(
                "failed()",
                transitions,
                "task %r has no failure transition, so a failure there "
                "would end the workflow silently" % name,
            )

    def test_the_start_script_is_given_the_pool_sheet(self):
        """--samples-info, not --sample-map.

        --sample-map elsewhere in this pack is the clinical
        Project,Run_nr,Sample_ID,Index_ID sheet. The start script refuses
        that file, so this would fail every run.
        """
        text = self.workflow_text()
        self.assertIn("--samples-info", text)
        self.assertNotIn("--sample-map", text)


class MarvinAction(ActionContract, unittest.TestCase):
    action_name = "run_longplex_demux_marvin"

    def test_the_pipeline_runs_over_ssh(self):
        tasks = self.workflow["tasks"]
        self.assertEqual(tasks["run_demux"]["action"], "core.remote")

    def test_ssh_timeout_outlasts_a_real_demux(self):
        """core.remote is synchronous; the timeout is the wall clock budget.

        run_analysis's run_pipeline allows 72h for the same reason. A
        default measured in minutes would kill every real run.
        """
        default = self.action["parameters"]["demux_timeout"]["default"]
        self.assertGreaterEqual(default, 72 * 3600)

    def test_interpolated_paths_are_single_quoted(self):
        """core.remote builds a remote command line, so quoting is the
        mitigation (docs/deferred_findings.md item 5)."""
        text = self.workflow_text()
        for flag in ("--inbox-path", "--samples-info", "--output", "--rename-map"):
            self.assertIn(
                "%s '<%%" % flag,
                text,
                "%s's value is interpolated unquoted into a remote shell" % flag,
            )


class MiarkaAction(ActionContract, unittest.TestCase):
    action_name = "run_longplex_demux_miarka"

    def test_submission_goes_through_the_typed_gateway_action(self):
        actions = {task["action"] for task in self.workflow["tasks"].values()}
        self.assertIn("ductus.submit_miarka_job", actions)
        self.assertNotIn(
            "core.local",
            actions,
            "the curl-through-a-shell pattern was replaced by "
            "ductus.submit_miarka_job",
        )

    def test_status_url_is_read_through_the_runner_wrapper(self):
        """result().result.status_url, not result().status_url.

        The bug this asserts against is live in
        actions/workflows/reheader_pacbio_bams_miarka.yaml, which is the file
        this workflow was modelled on.
        """
        text = self.workflow_text()
        self.assertIn("result().result.status_url", text)
        self.assertNotIn("<% result().status_url %>", text)

    def test_the_parameters_string_quotes_only_the_optional_value(self):
        """Deliberate asymmetry, and it is load-bearing.

        `parameters` is one string the processing-service appends to a
        command line, and whether it reaches a shell is unverified.

        rename_map must be quoted: unquoted and empty, the string ends with a
        valueless --rename-map and the start script refuses the run. Quoting
        is the only way an empty optional value survives -- at the cost of
        depending on the shell assumption.

        samples_info must NOT be quoted: unquoted works under either
        interpretation, so quoting it would make the REQUIRED flag depend on
        that same assumption for nothing. It is always populated, so it has
        no empty case to protect.
        """
        text = self.workflow_text()
        self.assertIn("--rename-map '<%", text)
        self.assertIn("--samples-info <%", text)
        self.assertNotIn("--samples-info '<%", text)

    def test_polling_verifies_the_gateway_certificate(self):
        """ductus.poll_status still defaults verify_ssl_cert to false, so
        every polling task has to pass it explicitly."""
        for name, task in self.workflow["tasks"].items():
            if task["action"] != "ductus.poll_status":
                continue
            self.assertIn(
                "verify_ssl_cert",
                task["input"],
                "poll task %r does not pass verify_ssl_cert, so it polls "
                "without validating the certificate" % name,
            )


class SharedConventions(unittest.TestCase):
    def test_both_actions_drive_the_same_deployed_script(self):
        """One script, two submission mechanisms -- as the reheader pair."""
        for name in ("run_longplex_demux_marvin", "run_longplex_demux_miarka"):
            action = load(os.path.join(ACTIONS, "%s.yaml" % name))
            self.assertEqual(
                action["parameters"]["longplex_demux_runscript_path"]["default"],
                "{{ config_context.longplex_demux_runscript_path }}",
            )

    def test_both_actions_accept_an_optional_rename_map(self):
        """The pipeline's own optional rename_map parameter.

        Default empty, which the start script reads as "not supplied", so
        neither workflow needs a YAQL conditional to leave the flag out.
        """
        for name in ("run_longplex_demux_marvin", "run_longplex_demux_miarka"):
            action = load(os.path.join(ACTIONS, "%s.yaml" % name))
            self.assertIn("rename_map", action["parameters"])
            self.assertEqual(action["parameters"]["rename_map"]["default"], "")
            self.assertFalse(action["parameters"]["rename_map"]["required"])
            workflow = load(os.path.join(ACTIONS, action["entry_point"]))
            self.assertIn("rename_map", workflow["input"])

    def test_both_actions_can_be_invoked_by_hand(self):
        """enabled: true, as the reheader actions are.

        st2 refuses to run a disabled action, and both descriptions -- and
        the summary doc -- tell an operator to run these with `st2 run`. The
        thing that keeps them from firing on their own is the absence of a
        rule, asserted separately below, not a disabled flag.

        The contrast is ductus.build_longplex_inputs_marvin, which is
        disabled for a different reason: its dest_dir could not exist at all
        when it was written. Here pool_sheet.csv can be produced today, by
        running scripts/build_longplex_inputs.py on the cluster by hand.
        """
        for name in ("run_longplex_demux_marvin", "run_longplex_demux_miarka"):
            action = load(os.path.join(ACTIONS, "%s.yaml" % name))
            self.assertTrue(
                action["enabled"],
                "%s is disabled, so the `st2 run` invocation its own "
                "description gives would be refused" % name,
            )

    def test_no_rule_wires_a_trigger_to_them(self):
        """The sensor cannot detect LongPlex at all -- seqWell barcodes never
        reach SMRT Link -- so a rule would be wired to a trigger that never
        fires for these runs."""
        rules_dir = os.path.join(REPO_ROOT, "rules")
        for entry in os.listdir(rules_dir):
            if not entry.endswith(".yaml"):
                continue
            with open(os.path.join(rules_dir, entry)) as handle:
                self.assertNotIn("run_longplex_demux", handle.read())


if __name__ == "__main__":
    unittest.main()
