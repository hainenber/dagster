from abc import ABC, ABCMeta, abstractmethod
from typing import (
    AbstractSet,
    Any,
    Dict,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    cast,
)

import dagster._check as check
from dagster._annotations import deprecated, experimental, public
from dagster._core.definitions.asset_check_spec import AssetCheckSpec
from dagster._core.definitions.assets import AssetsDefinition
from dagster._core.definitions.data_version import (
    DataProvenance,
    DataVersion,
    extract_data_provenance_from_entry,
)
from dagster._core.definitions.dependency import Node, NodeHandle
from dagster._core.definitions.events import (
    AssetKey,
    AssetMaterialization,
    AssetObservation,
    ExpectationResult,
    UserEvent,
)
from dagster._core.definitions.job_definition import JobDefinition
from dagster._core.definitions.op_definition import OpDefinition
from dagster._core.definitions.partition import PartitionsDefinition
from dagster._core.definitions.partition_key_range import PartitionKeyRange
from dagster._core.definitions.step_launcher import StepLauncher
from dagster._core.definitions.time_window_partitions import TimeWindow
from dagster._core.errors import (
    DagsterInvalidPropertyError,
    DagsterInvariantViolationError,
)
from dagster._core.events import DagsterEvent
from dagster._core.instance import DagsterInstance
from dagster._core.log_manager import DagsterLogManager
from dagster._core.storage.dagster_run import DagsterRun
from dagster._utils.forked_pdb import ForkedPdb
from dagster._utils.warnings import deprecation_warning

from .system import StepExecutionContext


# This metaclass has to exist for OpExecutionContext to have a metaclass
class AbstractComputeMetaclass(ABCMeta):
    pass


class AbstractComputeExecutionContext(ABC, metaclass=AbstractComputeMetaclass):
    """Base class for op context implemented by OpExecutionContext and DagstermillExecutionContext."""

    @abstractmethod
    def has_tag(self, key: str) -> bool:
        """Implement this method to check if a logging tag is set."""

    @abstractmethod
    def get_tag(self, key: str) -> Optional[str]:
        """Implement this method to get a logging tag."""

    @property
    @abstractmethod
    def run_id(self) -> str:
        """The run id for the context."""

    @property
    @abstractmethod
    def op_def(self) -> OpDefinition:
        """The op definition corresponding to the execution step being executed."""

    @property
    @abstractmethod
    def job_def(self) -> JobDefinition:
        """The job being executed."""

    @property
    @abstractmethod
    def run(self) -> DagsterRun:
        """The DagsterRun object corresponding to the execution."""

    @property
    @abstractmethod
    def resources(self) -> Any:
        """Resources available in the execution context."""

    @property
    @abstractmethod
    def log(self) -> DagsterLogManager:
        """The log manager available in the execution context."""

    @property
    @abstractmethod
    def op_config(self) -> Any:
        """The parsed config specific to this op."""


class OpExecutionContextMetaClass(AbstractComputeMetaclass):
    def __instancecheck__(cls, instance) -> bool:
        # This makes isinstance(context, OpExecutionContext) throw a deprecation warning when
        # context is an AssetExecutionContext. This metaclass can be deleted once AssetExecutionContext
        # has been split into it's own class in 1.7.0
        if isinstance(instance, AssetExecutionContext):
            deprecation_warning(
                subject="AssetExecutionContext",
                additional_warn_text=(
                    "Starting in version 1.7.0 AssetExecutionContext will no longer be a subclass"
                    " of OpExecutionContext."
                ),
                breaking_version="1.7.0",
                stacklevel=1,
            )
        return super().__instancecheck__(instance)


class OpExecutionContext(AbstractComputeExecutionContext, metaclass=OpExecutionContextMetaClass):
    """The ``context`` object that can be made available as the first argument to the function
    used for computing an op or asset.

    This context object provides system information such as resources, config, and logging.

    To construct an execution context for testing purposes, use :py:func:`dagster.build_op_context`.

    Example:
        .. code-block:: python

            from dagster import op, OpExecutionContext

            @op
            def hello_world(context: OpExecutionContext):
                context.log.info("Hello, world!")
    """

    __slots__ = ["_step_execution_context"]

    def __init__(self, step_execution_context: StepExecutionContext):
        self._step_execution_context = check.inst_param(
            step_execution_context,
            "step_execution_context",
            StepExecutionContext,
        )
        self._pdb: Optional[ForkedPdb] = None
        self._events: List[DagsterEvent] = []
        self._output_metadata: Dict[str, Any] = {}

    @public
    @property
    def op_config(self) -> Any:
        """Any: The parsed config specific to this op."""
        return self._step_execution_context.op_config

    @property
    def dagster_run(self) -> DagsterRun:
        """PipelineRun: The current pipeline run."""
        return self._step_execution_context.dagster_run

    @property
    def run(self) -> DagsterRun:
        """DagsterRun: The current run."""
        return self.dagster_run

    @public
    @property
    def instance(self) -> DagsterInstance:
        """DagsterInstance: The current Dagster instance."""
        return self._step_execution_context.instance

    @public
    @property
    def pdb(self) -> ForkedPdb:
        """dagster.utils.forked_pdb.ForkedPdb: Gives access to pdb debugging from within the op.

        Example:
            .. code-block:: python

                @op
                def debug(context):
                    context.pdb.set_trace()
        """
        if self._pdb is None:
            self._pdb = ForkedPdb()

        return self._pdb

    @property
    def file_manager(self):
        """Deprecated access to the file manager.

        :meta private:
        """
        raise DagsterInvalidPropertyError(
            "You have attempted to access the file manager which has been moved to resources in"
            " 0.10.0. Please access it via `context.resources.file_manager` instead."
        )

    @public
    @property
    def resources(self) -> Any:
        """Resources: The currently available resources."""
        return self._step_execution_context.resources

    @property
    def step_launcher(self) -> Optional[StepLauncher]:
        """Optional[StepLauncher]: The current step launcher, if any."""
        return self._step_execution_context.step_launcher

    @public
    @property
    def run_id(self) -> str:
        """str: The id of the current execution's run."""
        return self._step_execution_context.run_id

    @public
    @property
    def run_config(self) -> Mapping[str, object]:
        """dict: The run config for the current execution."""
        return self._step_execution_context.run_config

    @public
    @property
    def job_def(self) -> JobDefinition:
        """JobDefinition: The currently executing pipeline."""
        return self._step_execution_context.job_def

    @public
    @property
    def job_name(self) -> str:
        """str: The name of the currently executing pipeline."""
        return self._step_execution_context.job_name

    @public
    @property
    def log(self) -> DagsterLogManager:
        """DagsterLogManager: The log manager available in the execution context."""
        return self._step_execution_context.log

    @property
    def node_handle(self) -> NodeHandle:
        """NodeHandle: The current op's handle.

        :meta private:
        """
        return self._step_execution_context.node_handle

    @property
    def op_handle(self) -> NodeHandle:
        """NodeHandle: The current op's handle.

        :meta private:
        """
        return self.node_handle

    @property
    def op(self) -> Node:
        """Node: The object representing the invoked op within the graph.

        :meta private:

        """
        return self._step_execution_context.job_def.get_node(self.node_handle)

    @public
    @property
    def op_def(self) -> OpDefinition:
        """OpDefinition: The current op definition."""
        return cast(OpDefinition, self.op.definition)

    @public
    @property
    def has_partition_key(self) -> bool:
        """Whether the current run is a partitioned run."""
        return self._step_execution_context.has_partition_key

    @public
    @property
    def partition_key(self) -> str:
        """The partition key for the current run.

        Raises an error if the current run is not a partitioned run. Or if the current run is operating
        over a range of partitions (ie. a backfill of several partitions executed in a single run).

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def my_asset(context: AssetExecutionContext):
                    context.log.info(context.partition_key)

                # materializing the 2023-08-21 partition of this asset will log:
                #   "2023-08-21"
        """
        return self._step_execution_context.partition_key

    @deprecated(breaking_version="2.0", additional_warn_text="Use `partition_key_range` instead.")
    @public
    @property
    def asset_partition_key_range(self) -> PartitionKeyRange:
        """The range of partition keys for the current run.

        If run is for a single partition key, return a `PartitionKeyRange` with the same start and
        end. Raises an error if the current run is not a partitioned run.
        """
        return self.partition_key_range

    @public
    @property
    def partition_key_range(self) -> PartitionKeyRange:
        """The range of partition keys for the current run.

        If run is for a single partition key, returns a `PartitionKeyRange` with the same start and
        end. Raises an error if the current run is not a partitioned run.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def my_asset(context: AssetExecutionContext):
                    context.log.info(context.partition_key_range)

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   PartitionKeyRange(start="2023-08-21", end="2023-08-25")
        """
        return self._step_execution_context.asset_partition_key_range

    @public
    @property
    def partition_time_window(self) -> TimeWindow:
        """The partition time window for the current run.

        Raises an error if the current run is not a partitioned run, or if the job's partition
        definition is not a TimeWindowPartitionsDefinition.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def my_asset(context: AssetExecutionContext):
                    context.log.info(context.partition_time_window)

                # materializing the 2023-08-21 partition of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-22")
        """
        return self._step_execution_context.partition_time_window

    @public
    def has_tag(self, key: str) -> bool:
        """Check if a logging tag is set.

        Args:
            key (str): The tag to check.

        Returns:
            bool: Whether the tag is set.
        """
        return self._step_execution_context.has_tag(key)

    @public
    def get_tag(self, key: str) -> Optional[str]:
        """Get a logging tag.

        Args:
            key (tag): The tag to get.

        Returns:
            Optional[str]: The value of the tag, if present.
        """
        return self._step_execution_context.get_tag(key)

    @property
    def run_tags(self) -> Mapping[str, str]:
        """Mapping[str, str]: The tags for the current run."""
        return self._step_execution_context.run_tags

    def has_events(self) -> bool:
        return bool(self._events)

    def consume_events(self) -> Iterator[DagsterEvent]:
        """Pops and yields all user-generated events that have been recorded from this context.

        If consume_events has not yet been called, this will yield all logged events since the beginning of the op's computation. If consume_events has been called, it will yield all events since the last time consume_events was called. Designed for internal use. Users should never need to invoke this method.
        """
        events = self._events
        self._events = []
        yield from events

    @public
    def log_event(self, event: UserEvent) -> None:
        """Log an AssetMaterialization, AssetObservation, or ExpectationResult from within the body of an op.

        Events logged with this method will appear in the list of DagsterEvents, as well as the event log.

        Args:
            event (Union[AssetMaterialization, AssetObservation, ExpectationResult]): The event to log.

        **Examples:**

        .. code-block:: python

            from dagster import op, AssetMaterialization

            @op
            def log_materialization(context):
                context.log_event(AssetMaterialization("foo"))
        """
        if isinstance(event, AssetMaterialization):
            self._events.append(
                DagsterEvent.asset_materialization(self._step_execution_context, event)
            )
        elif isinstance(event, AssetObservation):
            self._events.append(DagsterEvent.asset_observation(self._step_execution_context, event))
        elif isinstance(event, ExpectationResult):
            self._events.append(
                DagsterEvent.step_expectation_result(self._step_execution_context, event)
            )
        else:
            check.failed(f"Unexpected event {event}")

    @public
    def add_output_metadata(
        self,
        metadata: Mapping[str, Any],
        output_name: Optional[str] = None,
        mapping_key: Optional[str] = None,
    ) -> None:
        """Add metadata to one of the outputs of an op.

        This can be invoked multiple times per output in the body of an op. If the same key is
        passed multiple times, the value associated with the last call will be used.

        Args:
            metadata (Mapping[str, Any]): The metadata to attach to the output
            output_name (Optional[str]): The name of the output to attach metadata to. If there is only one output on the op, then this argument does not need to be provided. The metadata will automatically be attached to the only output.
            mapping_key (Optional[str]): The mapping key of the output to attach metadata to. If the
                output is not dynamic, this argument does not need to be provided.

        **Examples:**

        .. code-block:: python

            from dagster import Out, op
            from typing import Tuple

            @op
            def add_metadata(context):
                context.add_output_metadata({"foo", "bar"})
                return 5 # Since the default output is called "result", metadata will be attached to the output "result".

            @op(out={"a": Out(), "b": Out()})
            def add_metadata_two_outputs(context) -> Tuple[str, int]:
                context.add_output_metadata({"foo": "bar"}, output_name="b")
                context.add_output_metadata({"baz": "bat"}, output_name="a")

                return ("dog", 5)

        """
        metadata = check.mapping_param(metadata, "metadata", key_type=str)
        output_name = check.opt_str_param(output_name, "output_name")
        mapping_key = check.opt_str_param(mapping_key, "mapping_key")

        self._step_execution_context.add_output_metadata(
            metadata=metadata, output_name=output_name, mapping_key=mapping_key
        )

    def get_output_metadata(
        self, output_name: str, mapping_key: Optional[str] = None
    ) -> Optional[Mapping[str, Any]]:
        return self._step_execution_context.get_output_metadata(
            output_name=output_name, mapping_key=mapping_key
        )

    def get_step_execution_context(self) -> StepExecutionContext:
        """Allows advanced users (e.g. framework authors) to punch through to the underlying
        step execution context.

        :meta private:

        Returns:
            StepExecutionContext: The underlying system context.
        """
        return self._step_execution_context

    @public
    @property
    def retry_number(self) -> int:
        """Which retry attempt is currently executing i.e. 0 for initial attempt, 1 for first retry, etc."""
        return self._step_execution_context.previous_attempt_count

    def describe_op(self):
        return self._step_execution_context.describe_op()

    @public
    def get_mapping_key(self) -> Optional[str]:
        """Which mapping_key this execution is for if downstream of a DynamicOutput, otherwise None."""
        return self._step_execution_context.step.get_mapping_key()

    #############################################################################################
    # asset related methods
    #############################################################################################

    @public
    @property
    def asset_key(self) -> AssetKey:
        """The AssetKey for the current asset. In a multi_asset, use asset_key_for_output instead."""
        if self.has_assets_def and len(self.assets_def.keys_by_output_name.keys()) > 1:
            raise DagsterInvariantViolationError(
                "Cannot call `context.asset_key` in a multi_asset with more than one asset. Use"
                " `context.asset_key_for_output` instead."
            )
        # pass in the output name to handle the case when a multi_asset has a single AssetOut
        return self.asset_key_for_output(
            output_name=next(iter(self.assets_def.keys_by_output_name.keys()))
        )

    @public
    @property
    def has_assets_def(self) -> bool:
        """If there is a backing AssetsDefinition for what is currently executing."""
        assets_def = self.job_def.asset_layer.assets_def_for_node(self.node_handle)
        return assets_def is not None

    @public
    @property
    def assets_def(self) -> AssetsDefinition:
        """The backing AssetsDefinition for what is currently executing, errors if not available."""
        assets_def = self.job_def.asset_layer.assets_def_for_node(self.node_handle)
        if assets_def is None:
            raise DagsterInvalidPropertyError(
                f"Op '{self.op.name}' does not have an assets definition."
            )
        return assets_def

    @public
    @property
    def selected_asset_keys(self) -> AbstractSet[AssetKey]:
        """Get the set of AssetKeys this execution is expected to materialize."""
        if not self.has_assets_def:
            return set()
        return self.assets_def.keys

    @public
    @property
    def selected_output_names(self) -> AbstractSet[str]:
        """Get the output names that correspond to the current selection of assets this execution is expected to materialize."""
        # map selected asset keys to the output names they correspond to
        selected_asset_keys = self.selected_asset_keys
        selected_outputs: Set[str] = set()
        for output_name in self.op.output_dict.keys():
            asset_info = self.job_def.asset_layer.asset_info_for_output(
                self.node_handle, output_name
            )
            if any(  #  For graph-backed assets, check if a downstream asset is selected
                [
                    asset_key in selected_asset_keys
                    for asset_key in self.job_def.asset_layer.downstream_dep_assets(
                        self.node_handle, output_name
                    )
                ]
            ) or (asset_info and asset_info.key in selected_asset_keys):
                selected_outputs.add(output_name)

        return selected_outputs

    @public
    def asset_key_for_output(self, output_name: str = "result") -> AssetKey:
        """Return the AssetKey for the corresponding output."""
        asset_output_info = self.job_def.asset_layer.asset_info_for_output(
            node_handle=self.op_handle, output_name=output_name
        )
        if asset_output_info is None:
            check.failed(f"Output '{output_name}' has no asset")
        else:
            return asset_output_info.key

    @public
    def output_for_asset_key(self, asset_key: AssetKey) -> str:
        """Return the output name for the corresponding asset key."""
        node_output_handle = self.job_def.asset_layer.node_output_handle_for_asset(asset_key)
        if node_output_handle is None:
            check.failed(f"Asset key '{asset_key}' has no output")
        else:
            return node_output_handle.output_name

    @public
    def asset_key_for_input(self, input_name: str) -> AssetKey:
        """Return the AssetKey for the corresponding input."""
        key = self.job_def.asset_layer.asset_key_for_input(
            node_handle=self.op_handle, input_name=input_name
        )
        if key is None:
            check.failed(f"Input '{input_name}' has no asset")
        else:
            return key

    @public
    def asset_partition_key_for_output(self, output_name: str = "result") -> str:
        """Returns the asset partition key for the given output.

        Args:
            output_name (str): For assets defined with the ``@asset`` decorator, the name of the output
                will be automatically provided. For assets defined with ``@multi_asset``, ``output_name``
                should be the op output associated with the asset key (as determined by AssetOut)
                to get the partition key for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def an_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partition_key_for_output())


                # materializing the 2023-08-21 partition of this asset will log:
                #   "2023-08-21"

                @multi_asset(
                    outs={
                        "first_asset": AssetOut(key=["my_assets", "first_asset"]),
                        "second_asset": AssetOut(key=["my_assets", "second_asset"])
                    }
                    partitions_def=partitions_def,
                )
                def a_multi_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partition_key_for_output("first_asset"))
                    context.log.info(context.asset_partition_key_for_output("second_asset"))


                # materializing the 2023-08-21 partition of this asset will log:
                #   "2023-08-21"
                #   "2023-08-21"


                @asset(
                    partitions_def=partitions_def,
                    ins={
                        "self_dependent_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                )
                def self_dependent_asset(context: AssetExecutionContext, self_dependent_asset):
                    context.log.info(context.asset_partition_key_for_output())

                # materializing the 2023-08-21 partition of this asset will log:
                #   "2023-08-21"

        """
        return self._step_execution_context.asset_partition_key_for_output(output_name)

    @public
    def asset_partitions_time_window_for_output(self, output_name: str = "result") -> TimeWindow:
        """The time window for the partitions of the output asset.

        If you want to write your asset to support running a backfill of several partitions in a single run,
        you can use ``asset_partitions_time_window_for_output`` to get the TimeWindow of all of the partitions
        being materialized by the backfill.

        Raises an error if either of the following are true:
        - The output asset has no partitioning.
        - The output asset is not partitioned with a TimeWindowPartitionsDefinition or a
        MultiPartitionsDefinition with one time-partitioned dimension.

        Args:
            output_name (str): For assets defined with the ``@asset`` decorator, the name of the output
                will be automatically provided. For assets defined with ``@multi_asset``, ``output_name``
                should be the op output associated with the asset key (as determined by AssetOut)
                to get the time window for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def an_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partitions_time_window_for_output())


                # materializing the 2023-08-21 partition of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-22")

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-26")

                @multi_asset(
                    outs={
                        "first_asset": AssetOut(key=["my_assets", "first_asset"]),
                        "second_asset": AssetOut(key=["my_assets", "second_asset"])
                    }
                    partitions_def=partitions_def,
                )
                def a_multi_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partitions_time_window_for_output("first_asset"))
                    context.log.info(context.asset_partitions_time_window_for_output("second_asset"))

                # materializing the 2023-08-21 partition of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-22")
                #   TimeWindow("2023-08-21", "2023-08-22")

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-26")
                #   TimeWindow("2023-08-21", "2023-08-26")


                @asset(
                    partitions_def=partitions_def,
                    ins={
                        "self_dependent_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                )
                def self_dependent_asset(context: AssetExecutionContext, self_dependent_asset):
                    context.log.info(context.asset_partitions_time_window_for_output())

                # materializing the 2023-08-21 partition of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-22")

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-26")

        """
        return self._step_execution_context.asset_partitions_time_window_for_output(output_name)

    @public
    def asset_partition_key_range_for_output(
        self, output_name: str = "result"
    ) -> PartitionKeyRange:
        """Return the PartitionKeyRange for the corresponding output. Errors if the run is not partitioned.

        If you want to write your asset to support running a backfill of several partitions in a single run,
        you can use ``asset_partition_key_range_for_output`` to get all of the partitions being materialized
        by the backfill.

        Args:
            output_name (str): For assets defined with the ``@asset`` decorator, the name of the output
                will be automatically provided. For assets defined with ``@multi_asset``, ``output_name``
                should be the op output associated with the asset key (as determined by AssetOut)
                to get the partition key range for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def an_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partition_key_range_for_output())


                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   PartitionKeyRange(start="2023-08-21", end="2023-08-25")

                @multi_asset(
                    outs={
                        "first_asset": AssetOut(key=["my_assets", "first_asset"]),
                        "second_asset": AssetOut(key=["my_assets", "second_asset"])
                    }
                    partitions_def=partitions_def,
                )
                def a_multi_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partition_key_range_for_output("first_asset"))
                    context.log.info(context.asset_partition_key_range_for_output("second_asset"))


                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   PartitionKeyRange(start="2023-08-21", end="2023-08-25")
                #   PartitionKeyRange(start="2023-08-21", end="2023-08-25")


                @asset(
                    partitions_def=partitions_def,
                    ins={
                        "self_dependent_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                )
                def self_dependent_asset(context: AssetExecutionContext, self_dependent_asset):
                    context.log.info(context.asset_partition_key_range_for_output())

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   PartitionKeyRange(start="2023-08-21", end="2023-08-25")

        """
        return self._step_execution_context.asset_partition_key_range_for_output(output_name)

    @public
    def asset_partition_key_range_for_input(self, input_name: str) -> PartitionKeyRange:
        """Return the PartitionKeyRange for the corresponding input. Errors if the asset depends on a
        non-contiguous chunk of the input.

        If you want to write your asset to support running a backfill of several partitions in a single run,
        you can use ``asset_partition_key_range_for_input`` to get the range of partitions keys of the input that
        are relevant to that backfill.

        Args:
            input_name (str): The name of the input to get the time window for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def upstream_asset():
                    ...

                @asset(
                    partitions_def=partitions_def
                )
                def an_asset(context: AssetExecutionContext, upstream_asset):
                    context.log.info(context.asset_partition_key_range_for_input("upstream_asset"))


                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   PartitionKeyRange(start="2023-08-21", end="2023-08-25")

                @asset(
                    ins={
                        "upstream_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                    partitions_def=partitions_def,
                )
                def another_asset(context: AssetExecutionContext, upstream_asset):
                    context.log.info(context.asset_partition_key_range_for_input("upstream_asset"))


                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   PartitionKeyRange(start="2023-08-20", end="2023-08-24")


                @asset(
                    partitions_def=partitions_def,
                    ins={
                        "self_dependent_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                )
                def self_dependent_asset(context: AssetExecutionContext, self_dependent_asset):
                    context.log.info(context.asset_partition_key_range_for_input("self_dependent_asset"))

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   PartitionKeyRange(start="2023-08-20", end="2023-08-24")


        """
        return self._step_execution_context.asset_partition_key_range_for_input(input_name)

    @public
    def asset_partition_key_for_input(self, input_name: str) -> str:
        """Returns the partition key of the upstream asset corresponding to the given input.

        Args:
            input_name (str): The name of the input to get the partition key for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def upstream_asset():
                    ...

                @asset(
                    partitions_def=partitions_def
                )
                def an_asset(context: AssetExecutionContext, upstream_asset):
                    context.log.info(context.asset_partition_key_for_input("upstream_asset"))

                # materializing the 2023-08-21 partition of this asset will log:
                #   "2023-08-21"


                @asset(
                    partitions_def=partitions_def,
                    ins={
                        "self_dependent_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                )
                def self_dependent_asset(context: AssetExecutionContext, self_dependent_asset):
                    context.log.info(context.asset_partition_key_for_input("self_dependent_asset"))

                # materializing the 2023-08-21 partition of this asset will log:
                #   "2023-08-20"

        """
        return self._step_execution_context.asset_partition_key_for_input(input_name)

    @public
    def asset_partitions_def_for_output(self, output_name: str = "result") -> PartitionsDefinition:
        """The PartitionsDefinition on the asset corresponding to this output.

        Args:
            output_name (str): For assets defined with the ``@asset`` decorator, the name of the output
                will be automatically provided. For assets defined with ``@multi_asset``, ``output_name``
                should be the op output associated with the asset key (as determined by AssetOut)
                to get the PartitionsDefinition for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def upstream_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partitions_def_for_output())

                # materializing the 2023-08-21 partition of this asset will log:
                #   DailyPartitionsDefinition("2023-08-20")

                @multi_asset(
                    outs={
                        "first_asset": AssetOut(key=["my_assets", "first_asset"]),
                        "second_asset": AssetOut(key=["my_assets", "second_asset"])
                    }
                    partitions_def=partitions_def,
                )
                def a_multi_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partitions_def_for_output("first_asset"))
                    context.log.info(context.asset_partitions_def_for_output("second_asset"))

                # materializing the 2023-08-21 partition of this asset will log:
                #   DailyPartitionsDefinition("2023-08-20")
                #   DailyPartitionsDefinition("2023-08-20")

        """
        asset_key = self.asset_key_for_output(output_name)
        result = self._step_execution_context.job_def.asset_layer.partitions_def_for_asset(
            asset_key
        )
        if result is None:
            raise DagsterInvariantViolationError(
                f"Attempting to access partitions def for asset {asset_key}, but it is not"
                " partitioned"
            )

        return result

    @public
    def asset_partitions_def_for_input(self, input_name: str) -> PartitionsDefinition:
        """The PartitionsDefinition on the upstream asset corresponding to this input.

        Args:
            input_name (str): The name of the input to get the PartitionsDefinition for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def upstream_asset():
                    ...

                @asset(
                    partitions_def=partitions_def
                )
                def upstream_asset(context: AssetExecutionContext, upstream_asset):
                    context.log.info(context.asset_partitions_def_for_input("upstream_asset"))

                # materializing the 2023-08-21 partition of this asset will log:
                #   DailyPartitionsDefinition("2023-08-20")

        """
        asset_key = self.asset_key_for_input(input_name)
        result = self._step_execution_context.job_def.asset_layer.partitions_def_for_asset(
            asset_key
        )
        if result is None:
            raise DagsterInvariantViolationError(
                f"Attempting to access partitions def for asset {asset_key}, but it is not"
                " partitioned"
            )

        return result

    @public
    def asset_partition_keys_for_output(self, output_name: str = "result") -> Sequence[str]:
        """Returns a list of the partition keys for the given output.

        If you want to write your asset to support running a backfill of several partitions in a single run,
        you can use ``asset_partition_keys_for_output`` to get all of the partitions being materialized
        by the backfill.

        Args:
            output_name (str): For assets defined with the ``@asset`` decorator, the name of the output
                will be automatically provided. For assets defined with ``@multi_asset``, ``output_name``
                should be the op output associated with the asset key (as determined by AssetOut)
                to get the partition keys for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def an_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partition_keys_for_output())


                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   ["2023-08-21", "2023-08-22", "2023-08-23", "2023-08-24", "2023-08-25"]

                @multi_asset(
                    outs={
                        "first_asset": AssetOut(key=["my_assets", "first_asset"]),
                        "second_asset": AssetOut(key=["my_assets", "second_asset"])
                    }
                    partitions_def=partitions_def,
                )
                def a_multi_asset(context: AssetExecutionContext):
                    context.log.info(context.asset_partition_keys_for_output("first_asset"))
                    context.log.info(context.asset_partition_keys_for_output("second_asset"))


                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   ["2023-08-21", "2023-08-22", "2023-08-23", "2023-08-24", "2023-08-25"]
                #   ["2023-08-21", "2023-08-22", "2023-08-23", "2023-08-24", "2023-08-25"]


                @asset(
                    partitions_def=partitions_def,
                    ins={
                        "self_dependent_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                )
                def self_dependent_asset(context: AssetExecutionContext, self_dependent_asset):
                    context.log.info(context.asset_partition_keys_for_output())

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   ["2023-08-21", "2023-08-22", "2023-08-23", "2023-08-24", "2023-08-25"]
        """
        return self.asset_partitions_def_for_output(output_name).get_partition_keys_in_range(
            self._step_execution_context.asset_partition_key_range_for_output(output_name),
            dynamic_partitions_store=self.instance,
        )

    @public
    def asset_partition_keys_for_input(self, input_name: str) -> Sequence[str]:
        """Returns a list of the partition keys of the upstream asset corresponding to the
        given input.

        If you want to write your asset to support running a backfill of several partitions in a single run,
        you can use ``asset_partition_keys_for_input`` to get all of the partition keys of the input that
        are relevant to that backfill.

        Args:
            input_name (str): The name of the input to get the time window for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def upstream_asset():
                    ...

                @asset(
                    partitions_def=partitions_def
                )
                def an_asset(context: AssetExecutionContext, upstream_asset):
                    context.log.info(context.asset_partition_keys_for_input("upstream_asset"))


                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   ["2023-08-21", "2023-08-22", "2023-08-23", "2023-08-24", "2023-08-25"]

                @asset(
                    ins={
                        "upstream_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                    partitions_def=partitions_def,
                )
                def another_asset(context: AssetExecutionContext, upstream_asset):
                    context.log.info(context.asset_partition_keys_for_input("upstream_asset"))


                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   ["2023-08-20", "2023-08-21", "2023-08-22", "2023-08-23", "2023-08-24"]


                @asset(
                    partitions_def=partitions_def,
                    ins={
                        "self_dependent_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                )
                def self_dependent_asset(context: AssetExecutionContext, self_dependent_asset):
                    context.log.info(context.asset_partition_keys_for_input("self_dependent_asset"))

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   ["2023-08-20", "2023-08-21", "2023-08-22", "2023-08-23", "2023-08-24"]
        """
        return list(
            self._step_execution_context.asset_partitions_subset_for_input(
                input_name
            ).get_partition_keys()
        )

    @public
    def asset_partitions_time_window_for_input(self, input_name: str = "result") -> TimeWindow:
        """The time window for the partitions of the input asset.

        If you want to write your asset to support running a backfill of several partitions in a single run,
        you can use ``asset_partitions_time_window_for_input`` to get the time window of the input that
        are relevant to that backfill.

        Raises an error if either of the following are true:
        - The input asset has no partitioning.
        - The input asset is not partitioned with a TimeWindowPartitionsDefinition or a
        MultiPartitionsDefinition with one time-partitioned dimension.

        Args:
            input_name (str): The name of the input to get the partition key for.

        Examples:
            .. code-block:: python

                partitions_def = DailyPartitionsDefinition("2023-08-20")

                @asset(
                    partitions_def=partitions_def
                )
                def upstream_asset():
                    ...

                @asset(
                    partitions_def=partitions_def
                )
                def an_asset(context: AssetExecutionContext, upstream_asset):
                    context.log.info(context.asset_partitions_time_window_for_input("upstream_asset"))


                # materializing the 2023-08-21 partition of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-22")

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-26")


                @asset(
                    ins={
                        "upstream_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                    partitions_def=partitions_def,
                )
                def another_asset(context: AssetExecutionContext, upstream_asset):
                    context.log.info(context.asset_partitions_time_window_for_input("upstream_asset"))


                # materializing the 2023-08-21 partition of this asset will log:
                #   TimeWindow("2023-08-20", "2023-08-21")

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   TimeWindow("2023-08-21", "2023-08-26")


                @asset(
                    partitions_def=partitions_def,
                    ins={
                        "self_dependent_asset": AssetIn(partition_mapping=TimeWindowPartitionMapping(start_offset=-1, end_offset=-1))
                    }
                )
                def self_dependent_asset(context: AssetExecutionContext, self_dependent_asset):
                    context.log.info(context.asset_partitions_time_window_for_input("self_dependent_asset"))

                # materializing the 2023-08-21 partition of this asset will log:
                #   TimeWindow("2023-08-20", "2023-08-21")

                # running a backfill of the 2023-08-21 through 2023-08-25 partitions of this asset will log:
                #   TimeWindow("2023-08-20", "2023-08-25")

        """
        return self._step_execution_context.asset_partitions_time_window_for_input(input_name)

    @public
    @experimental
    def get_asset_provenance(self, asset_key: AssetKey) -> Optional[DataProvenance]:
        """Return the provenance information for the most recent materialization of an asset.

        Args:
            asset_key (AssetKey): Key of the asset for which to retrieve provenance.

        Returns:
            Optional[DataProvenance]: Provenance information for the most recent
                materialization of the asset. Returns `None` if the asset was never materialized or
                the materialization record is too old to contain provenance information.
        """
        record = self.instance.get_latest_data_version_record(asset_key)

        return (
            None if record is None else extract_data_provenance_from_entry(record.event_log_entry)
        )

    def set_data_version(self, asset_key: AssetKey, data_version: DataVersion) -> None:
        """Set the data version for an asset being materialized by the currently executing step.
        This is useful for external execution situations where it is not possible to return
        an `Output`.

        Args:
            asset_key (AssetKey): Key of the asset for which to set the data version.
            data_version (DataVersion): The data version to set.
        """
        self._step_execution_context.set_data_version(asset_key, data_version)

    @property
    def asset_check_spec(self) -> AssetCheckSpec:
        asset_checks_def = check.not_none(
            self.job_def.asset_layer.asset_checks_def_for_node(self.node_handle),
            "This context does not correspond to an AssetChecksDefinition",
        )
        return asset_checks_def.spec


############################
##### AssetExecutionContext
############################

# To preserve backwards compatibility, AssetExecutionContext is being written as a subclass of
# OpExecutionContext until we can split it into its own class. All methods on OpExecutionContext
# that will not be included in the eventual AssetExecutionContext will be marked with deprecation
# warnings according to how the user should access that functionality in the future
#
# The following sets/maps are used to determine which methods need deprecation warnings, and how to
# direct users to the correct method to use


OP_EXECUTION_CONTEXT_ONLY_METHODS = set(
    [
        "describe_op",
        "file_manager",
        "has_assets_def",
        "get_mapping_key",
        # "get_step_execution_context", # used by internals
        "job_def",
        "node_handle",
        "op",
        "op_config",
        # "op_def", # used by internals
        "op_handle",
        "step_launcher",
        # "has_events", # used by internals
        "consume_events",
    ]
)


PARTITION_KEY_RANGE_AS_ALT = "use partition_key_range or partition_key_range_for_asset instead"
INPUT_OUTPUT_ALT = "not use input or output names and instead use asset keys directly"
OUTPUT_METADATA_ALT = "return MaterializeResult from the asset instead"

DEPRECATED_IO_MANAGER_CENTRIC_CONTEXT_METHODS = {
    "add_output_metadata": OUTPUT_METADATA_ALT,
    "asset_key_for_input": INPUT_OUTPUT_ALT,
    "asset_key_for_output": INPUT_OUTPUT_ALT,
    "asset_partition_key_for_input": PARTITION_KEY_RANGE_AS_ALT,
    "asset_partition_key_for_output": PARTITION_KEY_RANGE_AS_ALT,
    "asset_partition_key_range_for_input": PARTITION_KEY_RANGE_AS_ALT,
    "asset_partition_key_range_for_output": PARTITION_KEY_RANGE_AS_ALT,
    "asset_partition_keys_for_input": PARTITION_KEY_RANGE_AS_ALT,
    "asset_partition_keys_for_output": PARTITION_KEY_RANGE_AS_ALT,
    "asset_partitions_time_window_for_input": PARTITION_KEY_RANGE_AS_ALT,
    "asset_partitions_time_window_for_output": PARTITION_KEY_RANGE_AS_ALT,
    "asset_partitions_def_for_input": PARTITION_KEY_RANGE_AS_ALT,
    "asset_partitions_def_for_output": PARTITION_KEY_RANGE_AS_ALT,
    "get_output_metadata": "use op_execution_context.op_def.get_output(...).metadata",
    # "merge_output_metadata": OUTPUT_METADATA_ALT, # TODO - this method doesn't exist, check if it has a different name
    "output_for_asset_key": INPUT_OUTPUT_ALT,
    "selected_output_names": INPUT_OUTPUT_ALT,
}

ALTERNATE_AVAILABLE_METHODS = {
    "has_tag": (
        "use dagster_run.has_tag instead"
    ),  # TODO - was dagster_run intended to be a method/attr on AssetExecutionContext?
    "get_tag": "use dagster_run.get_tag instead",
    "run_tags": "use dagster_run.tags instead",
    "set_data_version": "use MaterializeResult instead",
}

# TODO - add AssetCheck related methods to this list


def _get_deprecation_kwargs(attr: str):
    deprecation_kwargs = {"breaking_version": "1.7.0"}
    deprecation_kwargs["subject"] = f"AssetExecutionContext.{attr}"

    if attr in OP_EXECUTION_CONTEXT_ONLY_METHODS:
        deprecation_kwargs["additional_warn_text"] = (
            f"You have called the deprecated method {attr} on AssetExecutionContext. Use"
            " the underlying OpExecutionContext instead by calling"
            f" context.op_execution_context.{attr}."
        )

    if attr in DEPRECATED_IO_MANAGER_CENTRIC_CONTEXT_METHODS:
        alt = DEPRECATED_IO_MANAGER_CENTRIC_CONTEXT_METHODS[attr]
        deprecation_kwargs["additional_warn_text"] = (
            f"You have called method {attr} on AssetExecutionContext that is oriented"
            f" around I/O managers. If you not using I/O managers we suggest you {alt}. If"
            " you are using I/O managers the method still exists at"
            f" context.op_execution_context.{attr}."
        )

    if attr in ALTERNATE_AVAILABLE_METHODS:
        deprecation_kwargs["additional_warn_text"] = f"Instead {ALTERNATE_AVAILABLE_METHODS[attr]}."

    return deprecation_kwargs


class AssetExecutionContext(OpExecutionContext):
    def __init__(self, op_execution_context: OpExecutionContext) -> None:
        self._op_execution_context = check.inst_param(
            op_execution_context, "op_execution_context", OpExecutionContext
        )
        self._step_execution_context = (
            self._op_execution_context._step_execution_context  # noqa: SLF001
        )

    @public
    @property
    def op_execution_context(self) -> OpExecutionContext:
        return self._op_execution_context

    # IContext interface methods

    @property
    def is_asset_step(self) -> bool:
        return self.op_execution_context.has_assets_def

    @public
    @property
    def asset_key(self) -> AssetKey:
        return self._op_execution_context.asset_key

    @property
    def asset_keys(self) -> Sequence[AssetKey]:
        return list(self.op_execution_context.assets_def.keys_by_output_name.values())

    @property
    def provenance(self) -> Optional[DataProvenance]:
        return self.get_asset_provenance(self.asset_key)

    @property
    def provenance_by_asset_key(self) -> Mapping[AssetKey, Optional[DataProvenance]]:
        provenance_map = {}
        for key in self.asset_keys:
            provenance_map[key] = self.get_asset_provenance(key)

        return provenance_map

    @property
    def code_version(self) -> Optional[str]:
        return self.get_assets_code_version([self.asset_key])[self.asset_key]

    @property
    def code_version_by_asset_key(self) -> Mapping[AssetKey, Optional[str]]:
        return self.get_assets_code_version(self.asset_keys)

    @public
    @property
    def is_partition_step(self) -> bool:
        return self._op_execution_context.has_partition_key

    @property
    def partition_key(self) -> str:
        return self.op_execution_context.partition_key

    @public
    @property
    def partition_key_range(self) -> PartitionKeyRange:
        return self._op_execution_context.asset_partition_key_range

    @property
    def partition_time_window(self) -> TimeWindow:
        return self.op_execution_context.partition_time_window

    @public
    @property
    def run_id(self) -> str:
        return self._op_execution_context.run_id

    @property
    def job_name(self) -> Optional[str]:
        return self.op_execution_context.job_name

    @property
    def retry_number(self) -> int:
        return self.op_execution_context.retry_number

    # Additional methods

    @public
    @property
    def dagster_run(self) -> DagsterRun:
        """PipelineRun: The current pipeline run."""
        return self._step_execution_context.dagster_run

    @public
    @property
    def pdb(self) -> ForkedPdb:
        return self._op_execution_context.pdb

    @public
    @property
    def log(self) -> DagsterLogManager:
        """DagsterLogManager: The log manager available in the execution context."""
        return self._op_execution_context.log

    @public
    def log_event(self, event: UserEvent) -> None:
        return self._op_execution_context.log_event(event)

    @public
    @property
    def assets_def(self) -> AssetsDefinition:
        return self._op_execution_context.assets_def

    @public
    @property
    def selected_asset_keys(self) -> AbstractSet[AssetKey]:
        return self._op_execution_context.selected_asset_keys

    @public
    @experimental
    def get_asset_provenance(self, asset_key: AssetKey) -> Optional[DataProvenance]:
        return self._op_execution_context.get_asset_provenance(asset_key)

    @public
    # TODO - method naming. this needs work
    def get_assets_code_version(
        self, asset_keys: Sequence[AssetKey]
    ) -> Mapping[AssetKey, Optional[str]]:
        return self.op_execution_context.instance.get_latest_materialization_code_versions(
            asset_keys
        )

    @property
    def asset_check_spec(self) -> AssetCheckSpec:
        return self._op_execution_context.asset_check_spec

    @public
    def partition_key_range_for_asset_key(self, asset_key: AssetKey) -> PartitionKeyRange:
        """TODO - implement in stacked pr."""
        pass

    # deprecated methods. All remaining methods on OpExecutionContext should be here with the
    # appropriate deprecation warning

    @deprecated(**_get_deprecation_kwargs("op_config"))
    @public
    @property
    def op_config(self) -> Any:
        return super().op_config

    @deprecated(**_get_deprecation_kwargs("file_manager"))
    @property
    def file_manager(self):
        return super().file_manager

    @deprecated(**_get_deprecation_kwargs("has_assets_def"))
    @public
    @property
    def has_assets_def(self) -> bool:
        return super().has_assets_def

    @deprecated(**_get_deprecation_kwargs("get_mapping_key"))
    @public
    def get_mapping_key(self) -> Optional[str]:
        return super().get_mapping_key()

    @deprecated(**_get_deprecation_kwargs("job_def"))
    @public
    @property
    def job_def(self) -> JobDefinition:
        return super().job_def

    @deprecated(**_get_deprecation_kwargs("node_handle"))
    @property
    def node_handle(self) -> NodeHandle:
        return super().node_handle

    @deprecated(**_get_deprecation_kwargs("op"))
    @property
    def op(self) -> Node:
        return super().op

    @deprecated(**_get_deprecation_kwargs("describe_op"))
    def describe_op(self):
        return super().describe_op()

    @deprecated(**_get_deprecation_kwargs("op_handle"))
    @property
    def op_handle(self) -> NodeHandle:
        return super().op_handle

    @deprecated(**_get_deprecation_kwargs("step_launcher"))
    @property
    def step_launcher(self) -> Optional[StepLauncher]:
        return super().step_launcher

    @deprecated(**_get_deprecation_kwargs("consume_events"))
    def consume_events(self) -> Iterator[DagsterEvent]:
        return super().consume_events()

    @deprecated(**_get_deprecation_kwargs("add_output_metadata"))
    @public
    def add_output_metadata(
        self,
        metadata: Mapping[str, Any],
        output_name: Optional[str] = None,
        mapping_key: Optional[str] = None,
    ) -> None:
        return super().add_output_metadata(
            metadata=metadata, output_name=output_name, mapping_key=mapping_key
        )

    @deprecated(**_get_deprecation_kwargs("asset_key_for_input"))
    @public
    def asset_key_for_input(self, input_name: str) -> AssetKey:
        return super().asset_key_for_input(input_name=input_name)

    @deprecated(**_get_deprecation_kwargs("asset_key_for_output"))
    @public
    def asset_key_for_output(self, output_name: str = "result") -> AssetKey:
        return super().asset_key_for_output(output_name=output_name)

    @deprecated(**_get_deprecation_kwargs("asset_partition_key_for_input"))
    @public
    def asset_partition_key_for_input(self, input_name: str) -> str:
        return super().asset_partition_key_for_input(input_name=input_name)

    @deprecated(**_get_deprecation_kwargs("asset_partition_key_for_output"))
    @public
    def asset_partition_key_for_output(self, output_name: str = "result") -> str:
        return super().asset_partition_key_for_output(output_name=output_name)

    @deprecated(**_get_deprecation_kwargs("asset_partition_key_range_for_input"))
    @public
    def asset_partition_key_range_for_input(self, input_name: str) -> PartitionKeyRange:
        return super().asset_partition_key_range_for_input(input_name=input_name)

    @deprecated(**_get_deprecation_kwargs("asset_partition_key_range_for_output"))
    @public
    def asset_partition_key_range_for_output(
        self, output_name: str = "result"
    ) -> PartitionKeyRange:
        return super().asset_partition_key_range_for_output(output_name=output_name)

    @deprecated(**_get_deprecation_kwargs("asset_partition_keys_for_input"))
    @public
    def asset_partition_keys_for_input(self, input_name: str) -> Sequence[str]:
        return super().asset_partition_keys_for_input(input_name=input_name)

    @deprecated(**_get_deprecation_kwargs("asset_partition_keys_for_output"))
    @public
    def asset_partition_keys_for_output(self, output_name: str = "result") -> Sequence[str]:
        return super().asset_partition_keys_for_output(output_name=output_name)

    @deprecated(**_get_deprecation_kwargs("asset_partitions_time_window_for_input"))
    @public
    def asset_partitions_time_window_for_input(self, input_name: str = "result") -> TimeWindow:
        return super().asset_partitions_time_window_for_input(input_name=input_name)

    @deprecated(**_get_deprecation_kwargs("asset_partitions_time_window_for_output"))
    @public
    def asset_partitions_time_window_for_output(self, output_name: str = "result") -> TimeWindow:
        return super().asset_partitions_time_window_for_output(output_name=output_name)

    @deprecated(**_get_deprecation_kwargs("asset_partitions_def_for_input"))
    @public
    def asset_partitions_def_for_input(self, input_name: str) -> PartitionsDefinition:
        return super().asset_partitions_def_for_input(input_name=input_name)

    @deprecated(**_get_deprecation_kwargs("asset_partitions_def_for_output"))
    @public
    def asset_partitions_def_for_output(self, output_name: str = "result") -> PartitionsDefinition:
        return super().asset_partitions_def_for_output(output_name=output_name)

    @deprecated(**_get_deprecation_kwargs("get_output_metadata"))
    def get_output_metadata(
        self, output_name: str, mapping_key: Optional[str] = None
    ) -> Optional[Mapping[str, Any]]:
        return super().get_output_metadata(output_name=output_name, mapping_key=mapping_key)

    @deprecated(**_get_deprecation_kwargs("output_for_asset_key"))
    @public
    def output_for_asset_key(self, asset_key: AssetKey) -> str:
        return super().output_for_asset_key(asset_key=asset_key)

    @deprecated(**_get_deprecation_kwargs("selected_output_names"))
    @public
    @property
    def selected_output_names(self) -> AbstractSet[str]:
        return super().selected_output_names

    @deprecated(**_get_deprecation_kwargs("has_tag"))
    @public
    def has_tag(self, key: str) -> bool:
        return super().has_tag(key=key)

    @deprecated(**_get_deprecation_kwargs("get_tag"))
    @public
    def get_tag(self, key: str) -> Optional[str]:
        return super().get_tag(key=key)

    @deprecated(**_get_deprecation_kwargs("run_tags"))
    @property
    def run_tags(self) -> Mapping[str, str]:
        return super().run_tags

    @deprecated(**_get_deprecation_kwargs("set_data_version"))
    def set_data_version(self, asset_key: AssetKey, data_version: DataVersion) -> None:
        return super().set_data_version(asset_key=asset_key, data_version=data_version)
