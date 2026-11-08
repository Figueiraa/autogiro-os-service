"""O span do consumo de cada evento registra como a Saga ficou depois dele."""

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.application.use_cases.saga_orchestrator import HandleResult
from app.domain.value_objects.service_order_status import ServiceOrderStatus
from app.infrastructure.messaging.runtime import tag_saga_result


def _span_com(result: HandleResult):
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with provider.get_tracer("teste").start_as_current_span("os.saga-events process"):
        tag_saga_result(result)
    (span,) = exporter.get_finished_spans()
    return span.attributes


def test_compensacao_registra_estado_motivo_e_status_da_os():
    atributos = _span_com(
        HandleResult(
            "applied",
            transitions=[(ServiceOrderStatus.EM_CANCELAMENTO, 10.0), (ServiceOrderStatus.CANCELADA, None)],
            saga_state="COMPENSADA",
            failure_reason="EXECUCAO_RECUSADA",
        )
    )
    assert atributos["autogiro.saga.outcome"] == "applied"
    assert atributos["autogiro.saga.state"] == "COMPENSADA"
    assert atributos["autogiro.saga.failure_reason"] == "EXECUCAO_RECUSADA"
    assert atributos["autogiro.order.status"] == "CANCELADA"


def test_mensagem_repetida_so_registra_o_desfecho():
    atributos = _span_com(HandleResult("duplicate"))
    assert atributos == {"autogiro.saga.outcome": "duplicate"}
