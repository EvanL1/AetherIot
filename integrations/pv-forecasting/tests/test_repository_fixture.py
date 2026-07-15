from aether_pv_forecasting_processor import PvForecastProcessor
from aether_pv_forecasting_processor.processor import ProcessorPolicy


def test_package_exports_pv_processor() -> None:
    assert PvForecastProcessor is not None
    assert ProcessorPolicy().processor_id == "pv-forecasting-edge"
