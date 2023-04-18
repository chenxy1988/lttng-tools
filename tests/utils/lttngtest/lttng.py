#!/usr/bin/env python3
#
# Copyright (C) 2022 Jérémie Galarneau <jeremie.galarneau@efficios.com>
#
# SPDX-License-Identifier: GPL-2.0-only

from concurrent.futures import process
from . import lttngctl, logger, environment
import pathlib
import os
from typing import Callable, Optional, Type, Union
import shlex
import subprocess
import enum

"""
Implementation of the lttngctl interface based on the `lttng` command line client.
"""


class Unsupported(lttngctl.ControlException):
    def __init__(self, msg):
        # type: (str) -> None
        super().__init__(msg)


def _get_domain_option_name(domain):
    # type: (lttngctl.TracingDomain) -> str
    if domain == lttngctl.TracingDomain.User:
        return "userspace"
    elif domain == lttngctl.TracingDomain.Kernel:
        return "kernel"
    elif domain == lttngctl.TracingDomain.Log4j:
        return "log4j"
    elif domain == lttngctl.TracingDomain.JUL:
        return "jul"
    elif domain == lttngctl.TracingDomain.Python:
        return "python"
    else:
        raise Unsupported("Domain `{domain_name}` is not supported by the LTTng client")


def _get_context_type_name(context):
    # type: (lttngctl.ContextType) -> str
    if isinstance(context, lttngctl.VgidContextType):
        return "vgid"
    elif isinstance(context, lttngctl.VuidContextType):
        return "vuid"
    elif isinstance(context, lttngctl.VpidContextType):
        return "vpid"
    elif isinstance(context, lttngctl.JavaApplicationContextType):
        return "$app.{retriever}:{field}".format(
            retriever=context.retriever_name, field=context.field_name
        )
    else:
        raise Unsupported(
            "Context `{context_name}` is not supported by the LTTng client".format(
                type(context).__name__
            )
        )


class _Channel(lttngctl.Channel):
    def __init__(
        self,
        client,  # type: LTTngClient
        name,  # type: str
        domain,  # type: lttngctl.TracingDomain
        session,  # type: _Session
    ):
        self._client = client  # type: LTTngClient
        self._name = name  # type: str
        self._domain = domain  # type: lttngctl.TracingDomain
        self._session = session  # type: _Session

    def add_context(self, context_type):
        # type: (lttngctl.ContextType) -> None
        domain_option_name = _get_domain_option_name(self.domain)
        context_type_name = _get_context_type_name(context_type)
        self._client._run_cmd(
            "add-context --{domain_option_name} --type {context_type_name}".format(
                domain_option_name=domain_option_name,
                context_type_name=context_type_name,
            )
        )

    def add_recording_rule(self, rule):
        # type: (Type[lttngctl.EventRule]) -> None
        client_args = (
            "enable-event --session {session_name} --channel {channel_name}".format(
                session_name=self._session.name, channel_name=self.name
            )
        )
        if isinstance(rule, lttngctl.TracepointEventRule):
            domain_option_name = (
                "userspace"
                if isinstance(rule, lttngctl.UserTracepointEventRule)
                else "kernel"
            )
            client_args = client_args + " --{domain_option_name}".format(
                domain_option_name=domain_option_name
            )

            if rule.name_pattern:
                client_args = client_args + " " + rule.name_pattern
            else:
                client_args = client_args + " --all"

            if rule.filter_expression:
                client_args = client_args + " " + rule.filter_expression

            if rule.log_level_rule:
                if isinstance(rule.log_level_rule, lttngctl.LogLevelRuleAsSevereAs):
                    client_args = client_args + " --loglevel {log_level}".format(
                        log_level=rule.log_level_rule.level
                    )
                elif isinstance(rule.log_level_rule, lttngctl.LogLevelRuleExactly):
                    client_args = client_args + " --loglevel-only {log_level}".format(
                        log_level=rule.log_level_rule.level
                    )
                else:
                    raise Unsupported(
                        "Unsupported log level rule type `{log_level_rule_type}`".format(
                            log_level_rule_type=type(rule.log_level_rule).__name__
                        )
                    )

            if rule.name_pattern_exclusions:
                client_args = client_args + " --exclude "
                for idx, pattern in enumerate(rule.name_pattern_exclusions):
                    if idx != 0:
                        client_args = client_args + ","
                    client_args = client_args + pattern
        else:
            raise Unsupported(
                "event rule type `{event_rule_type}` is unsupported by LTTng client".format(
                    event_rule_type=type(rule).__name__
                )
            )

        self._client._run_cmd(client_args)

    @property
    def name(self):
        # type: () -> str
        return self._name

    @property
    def domain(self):
        # type: () -> lttngctl.TracingDomain
        return self._domain


@enum.unique
class _ProcessAttribute(enum.Enum):
    PID = "Process ID"
    VPID = "Virtual Process ID"
    UID = "User ID"
    VUID = "Virtual User ID"
    GID = "Group ID"
    VGID = "Virtual Group ID"

    def __repr__(self):
        return "<%s.%s>" % (self.__class__.__name__, self.name)


def _get_process_attribute_option_name(attribute):
    # type: (_ProcessAttribute) -> str
    return {
        _ProcessAttribute.PID: "pid",
        _ProcessAttribute.VPID: "vpid",
        _ProcessAttribute.UID: "uid",
        _ProcessAttribute.VUID: "vuid",
        _ProcessAttribute.GID: "gid",
        _ProcessAttribute.VGID: "vgid",
    }[attribute]


class _ProcessAttributeTracker(lttngctl.ProcessAttributeTracker):
    def __init__(
        self,
        client,  # type: LTTngClient
        attribute,  # type: _ProcessAttribute
        domain,  # type: lttngctl.TracingDomain
        session,  # type: _Session
    ):
        self._client = client  # type: LTTngClient
        self._tracked_attribute = attribute  # type: _ProcessAttribute
        self._domain = domain  # type: lttngctl.TracingDomain
        self._session = session  # type: _Session
        if attribute == _ProcessAttribute.PID or attribute == _ProcessAttribute.VPID:
            self._allowed_value_types = [int, str]  # type: list[type]
        else:
            self._allowed_value_types = [int]  # type: list[type]

    def _call_client(self, cmd_name, value):
        # type: (str, Union[int, str]) -> None
        if type(value) not in self._allowed_value_types:
            raise TypeError(
                "Value of type `{value_type}` is not allowed for process attribute {attribute_name}".format(
                    value_type=type(value).__name__,
                    attribute_name=self._tracked_attribute.name,
                )
            )

        process_attribute_option_name = _get_process_attribute_option_name(
            self._tracked_attribute
        )
        domain_name = _get_domain_option_name(self._domain)
        self._client._run_cmd(
            "{cmd_name} --session {session_name} --{domain_name} --{tracked_attribute_name} {value}".format(
                cmd_name=cmd_name,
                session_name=self._session.name,
                domain_name=domain_name,
                tracked_attribute_name=process_attribute_option_name,
                value=value,
            )
        )

    def track(self, value):
        # type: (Union[int, str]) -> None
        self._call_client("track", value)

    def untrack(self, value):
        # type: (Union[int, str]) -> None
        self._call_client("untrack", value)


class _Session(lttngctl.Session):
    def __init__(
        self,
        client,  # type: LTTngClient
        name,  # type: str
        output,  # type: Optional[lttngctl.SessionOutputLocation]
    ):
        self._client = client  # type: LTTngClient
        self._name = name  # type: str
        self._output = output  # type: Optional[lttngctl.SessionOutputLocation]

    @property
    def name(self):
        # type: () -> str
        return self._name

    def add_channel(self, domain, channel_name=None):
        # type: (lttngctl.TracingDomain, Optional[str]) -> lttngctl.Channel
        channel_name = lttngctl.Channel._generate_name()
        domain_option_name = _get_domain_option_name(domain)
        self._client._run_cmd(
            "enable-channel --{domain_name} {channel_name}".format(
                domain_name=domain_option_name, channel_name=channel_name
            )
        )
        return _Channel(self._client, channel_name, domain, self)

    def add_context(self, context_type):
        # type: (lttngctl.ContextType) -> None
        pass

    @property
    def output(self):
        # type: () -> "Optional[Type[lttngctl.SessionOutputLocation]]"
        return self._output  # type: ignore

    def start(self):
        # type: () -> None
        self._client._run_cmd("start {session_name}".format(session_name=self.name))

    def stop(self):
        # type: () -> None
        self._client._run_cmd("stop {session_name}".format(session_name=self.name))

    def destroy(self):
        # type: () -> None
        self._client._run_cmd("destroy {session_name}".format(session_name=self.name))

    @property
    def kernel_pid_process_attribute_tracker(self):
        # type: () -> Type[lttngctl.ProcessIDProcessAttributeTracker]
        return _ProcessAttributeTracker(self._client, _ProcessAttribute.PID, lttngctl.TracingDomain.Kernel, self)  # type: ignore

    @property
    def kernel_vpid_process_attribute_tracker(self):
        # type: () -> Type[lttngctl.VirtualProcessIDProcessAttributeTracker]
        return _ProcessAttributeTracker(self._client, _ProcessAttribute.VPID, lttngctl.TracingDomain.Kernel, self)  # type: ignore

    @property
    def user_vpid_process_attribute_tracker(self):
        # type: () -> Type[lttngctl.VirtualProcessIDProcessAttributeTracker]
        return _ProcessAttributeTracker(self._client, _ProcessAttribute.VPID, lttngctl.TracingDomain.User, self)  # type: ignore

    @property
    def kernel_gid_process_attribute_tracker(self):
        # type: () -> Type[lttngctl.GroupIDProcessAttributeTracker]
        return _ProcessAttributeTracker(self._client, _ProcessAttribute.GID, lttngctl.TracingDomain.Kernel, self)  # type: ignore

    @property
    def kernel_vgid_process_attribute_tracker(self):
        # type: () -> Type[lttngctl.VirtualGroupIDProcessAttributeTracker]
        return _ProcessAttributeTracker(self._client, _ProcessAttribute.VGID, lttngctl.TracingDomain.Kernel, self)  # type: ignore

    @property
    def user_vgid_process_attribute_tracker(self):
        # type: () -> Type[lttngctl.VirtualGroupIDProcessAttributeTracker]
        return _ProcessAttributeTracker(self._client, _ProcessAttribute.VGID, lttngctl.TracingDomain.User, self)  # type: ignore

    @property
    def kernel_uid_process_attribute_tracker(self):
        # type: () -> Type[lttngctl.UserIDProcessAttributeTracker]
        return _ProcessAttributeTracker(self._client, _ProcessAttribute.UID, lttngctl.TracingDomain.Kernel, self)  # type: ignore

    @property
    def kernel_vuid_process_attribute_tracker(self):
        # type: () -> Type[lttngctl.VirtualUserIDProcessAttributeTracker]
        return _ProcessAttributeTracker(self._client, _ProcessAttribute.VUID, lttngctl.TracingDomain.Kernel, self)  # type: ignore

    @property
    def user_vuid_process_attribute_tracker(self):
        # type: () -> Type[lttngctl.VirtualUserIDProcessAttributeTracker]
        return _ProcessAttributeTracker(self._client, _ProcessAttribute.VUID, lttngctl.TracingDomain.User, self)  # type: ignore


class LTTngClientError(lttngctl.ControlException):
    def __init__(
        self,
        command_args,  # type: str
        error_output,  # type: str
    ):
        self._command_args = command_args  # type: str
        self._output = error_output  # type: str


class LTTngClient(logger._Logger, lttngctl.Controller):
    """
    Implementation of a LTTngCtl Controller that uses the `lttng` client as a back-end.
    """

    def __init__(
        self,
        test_environment,  # type: environment._Environment
        log,  # type: Optional[Callable[[str], None]]
    ):
        logger._Logger.__init__(self, log)
        self._environment = test_environment  # type: environment._Environment

    def _run_cmd(self, command_args):
        # type: (str) -> None
        """
        Invoke the `lttng` client with a set of arguments. The command is
        executed in the context of the client's test environment.
        """
        args = [str(self._environment.lttng_client_path)]  # type: list[str]
        args.extend(shlex.split(command_args))

        self._log("lttng {command_args}".format(command_args=command_args))

        client_env = os.environ.copy()  # type: dict[str, str]
        client_env["LTTNG_HOME"] = str(self._environment.lttng_home_location)

        process = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=client_env
        )

        out = process.communicate()[0]

        if process.returncode != 0:
            decoded_output = out.decode("utf-8")
            for error_line in decoded_output.splitlines():
                self._log(error_line)
            raise LTTngClientError(command_args, decoded_output)

    def create_session(self, name=None, output=None):
        # type: (Optional[str], Optional[lttngctl.SessionOutputLocation]) -> lttngctl.Session
        name = name if name else lttngctl.Session._generate_name()

        if isinstance(output, lttngctl.LocalSessionOutputLocation):
            output_option = "--output {output_path}".format(output_path=output.path)
        elif output is None:
            output_option = "--no-output"
        else:
            raise TypeError("LTTngClient only supports local or no output")

        self._run_cmd(
            "create {session_name} {output_option}".format(
                session_name=name, output_option=output_option
            )
        )
        return _Session(self, name, output)