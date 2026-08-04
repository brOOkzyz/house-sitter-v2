"""Core planning and safety modules for house_sitter_v2."""

from .executor import DryRunExecutor, Nav2WaypointExecutor
from .llm_provider import (
    GeminiPlannerProvider,
    MockPlannerProvider,
    PlannerProvider,
    PlannerProviderError,
    RealLLMPlannerProvider,
    VerifiedPlannerAdapter,
    provider_from_env,
)
from .nav2_client import (
    Nav2ActionClient,
    NavigateToPoseSpec,
    WaypointConfigError,
    WaypointStore,
)
from .planner import MockPlanner
from .automatic_area_proposal import (
    AutoAreaProposal,
    AutomaticAreaProposalError,
    HoleAwareReviewBatch,
    OccupancyClassification,
    build_hole_aware_review_batch,
    classify_occupancy,
    propose_semantic_areas,
)
from .map_coordinates import map_to_pixel, pixel_to_map
from .map_metadata import MapMetadataError, PgmImage, RosMapMetadata, load_pgm, load_ros_map
from .semantic_annotation import SemanticAnnotationError, SemanticAnnotationSession
from .semantic_waypoints import (
    PolygonGeometry,
    SemanticArea,
    SemanticWaypointError,
    SemanticWaypointRegistry,
    resolve_semantic_label,
    semantic_label_exists,
)
from .sim_execution_request import (
    ExecutionRequestBuildResult,
    build_sim_nav2_execution_request,
    build_sim_nav2_execution_requests,
)
from .verifier import PlanVerificationError, PlanVerifier

__all__ = [
    "DryRunExecutor",
    "GeminiPlannerProvider",
    "MockPlannerProvider",
    "MockPlanner",
    "AutoAreaProposal",
    "AutomaticAreaProposalError",
    "HoleAwareReviewBatch",
    "OccupancyClassification",
    "build_hole_aware_review_batch",
    "classify_occupancy",
    "propose_semantic_areas",
    "MapMetadataError",
    "PgmImage",
    "RosMapMetadata",
    "load_pgm",
    "load_ros_map",
    "pixel_to_map",
    "map_to_pixel",
    "SemanticAnnotationError",
    "SemanticAnnotationSession",
    "Nav2ActionClient",
    "NavigateToPoseSpec",
    "Nav2WaypointExecutor",
    "PlanVerificationError",
    "PlannerProvider",
    "PlannerProviderError",
    "RealLLMPlannerProvider",
    "VerifiedPlannerAdapter",
    "SemanticWaypointError",
    "PolygonGeometry",
    "SemanticArea",
    "SemanticWaypointRegistry",
    "resolve_semantic_label",
    "semantic_label_exists",

    "PlanVerifier",
    "ExecutionRequestBuildResult",
    "build_sim_nav2_execution_request",
    "build_sim_nav2_execution_requests",
    "provider_from_env",
    "WaypointConfigError",
    "WaypointStore",
]
