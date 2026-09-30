import json
import sys

from okp.cli import main
from okp.operations.actions import ActionError
from okp.security.policy import AccessError
from okp.semantic.engine import MetricError

try:
    sys.exit(main())
except (AccessError, ActionError, MetricError) as exc:
    print(json.dumps({"code": exc.code, "message": exc.message, "details": getattr(exc, "details", {})},
                     ensure_ascii=False, indent=2, default=str), file=sys.stderr)
    sys.exit(3)
