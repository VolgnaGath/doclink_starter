"""CSV, Markdown and PNG are views of the same immutable saved dataset."""

import csv
import io
import json
from typing import Any, Literal

from app.services.model_calls import ProcessingError, fingerprint


def validate(dataset: dict[str, Any]) -> None:
    if not dataset or fingerprint(
        {k: v for k, v in dataset.items() if k != "dataset_hash"}
    ) != dataset.get("dataset_hash"):
        raise ProcessingError("Нет завершённого неизменного набора для экспорта")


def number(value: Any) -> str:
    if value is None:
        return "—"
    return f"{value:.5f}" if isinstance(value, float) else str(value)


def markdown(dataset: dict[str, Any]) -> str:
    validate(dataset)
    lines = [
        f"# {dataset['name']}",
        "",
        f"Dataset: `{dataset['dataset_hash']}`.",
        f"Корпус: `{dataset['corpus_version_id']}`.",
        "",
        *dataset["notes"],
        "",
        "## Таблица 2.6 — извлечение и исходные ресурсы моделей",
        "",
        "| Модель / размещение | Split | N | Вид | Номер | Дата | ИНН | Сумма | "
        "Невалидные | Ненайденные цитаты | Нет оснований | мс/док. | ₽/док. | "
        "Локально, с (расчётное) |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for model in dataset["table_2_6"]:
        for split, row in model["splits"].items():
            fields = " | ".join(
                number(row["fields"][field]["value"])
                for field in ("kind", "number", "date", "cp_inn", "amount")
            )
            res = row["resources"]
            lines.append(
                f"| {model['model']} / {model['placement']} | {split} | {row['n']} | {fields} | "
                f"{number(row['invalid']['value'])} | {number(row['unmatched_quotes']['value'])} | "
                f"{row['missing_evidence']} | {number(res['source_latency_per_document_ms'])} | "
                f"{res['source_cost_per_document_rub'] or '—'} | "
                f"{number(res['estimated_local_seconds'])} |"
            )
    lines.extend(
        [
            "",
            "Знаменатели полей и цитат сохранены в CSV/манифесте; неразмеченные поля исключены.",
            "Ресурсы — исходные физические вычисления, не время повторного извлечения из кэша.",
            "",
            "## Таблица 2.7 — сравнение основных методов",
            "",
            "| Вариант | Split | Статус | N | M | Верных | Gold | Precision | Recall | F1 | "
            "Охват | Док. точно | Разбор | Ошибки | Recall@3 |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in dataset["runs"]:
        if run["ablation"] != "none":
            continue
        alias = " (общий: Qwen/YandexGPT/GigaChat)" if run["baseline_models"] else ""
        for split in ("tune", "test"):
            row = run["report"].get("metrics", {}).get(split, {}).get("all")
            cells = (
                " | ".join(
                    number(row[k])
                    for k in (
                        "n",
                        "m",
                        "correct_links",
                        "gold_links",
                        "precision",
                        "recall",
                        "f1",
                        "coverage",
                        "document_accuracy",
                        "review_share",
                        "processing_errors",
                        "recall_at_3",
                    )
                )
                if row
                else " | ".join(["—"] * 12)
            )
            lines.append(f"| {run['variant']}{alias} | {split} | {run['status']} | {cells} |")
    lines.extend(
        [
            "",
            "## Таблица 2.8 — абляции и трудные срезы",
            "",
            "| Вариант | Split | Срез | N | M | Precision | Recall | F1 | Охват | Ошибки |",
            "|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for run in dataset["runs"]:
        if not run["report"].get("metrics"):
            lines.append(
                f"| {run['variant']} | — | {run['status']}: {run['error']} | "
                "— | — | — | — | — | — | — |"
            )
        for split, slices in run["report"].get("metrics", {}).items():
            for name, row in slices.items():
                if name == "all" or name.startswith("hard"):
                    cells = " | ".join(
                        number(row[k])
                        for k in (
                            "n",
                            "m",
                            "precision",
                            "recall",
                            "f1",
                            "coverage",
                            "processing_errors",
                        )
                    )
                    lines.append(f"| {run['variant']} | {split} | {name} | {cells} |")
    lines.extend(
        [
            "",
            "## Контрольные случаи (split указан, это не отдельная test-метрика)",
            "",
            "| Вариант | Документ | Split | Case | Ожидание | Исход | Причины | "
            "Пройдено | Результат |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )
    for run in dataset["runs"]:
        if run["method"] != "combined" or run["ablation"] != "none":
            continue
        for row in run["report"].get("controls", []):
            lines.append(
                f"| {run['variant']} | {row['document']} | {row['split']} | {row['case']} | "
                f"{row['expected']} | {row['actual']} | {', '.join(row['reasons'])} | "
                f"{'да' if row['passed'] else 'нет'} | {row['result_id']} |"
            )
    lines.extend(
        [
            "",
            "## Ресурсы и воспроизводимость",
            "",
            f"Новые физические LLM-вызовы: {dataset['resources']['new_physical_llm_calls']}; "
            f"новый API-расход: {dataset['resources']['new_llm_cost_rub']} ₽.",
            "Стоимость уникальных исходных вызовов серии: "
            f"{dataset['resources']['source_cost_rub']} ₽; "
            "фактическое денежное списание не установлено.",
            "",
            "| Вариант | reused_test | τ_a | τ_r | Переиспользовано исходов | мс link | "
            "Хеш протокола |",
            "|---|---|---:|---:|---:|---:|---|",
        ]
    )
    for run in dataset["runs"]:
        res = run["report"].get("resources", {})
        lines.append(
            f"| {run['variant']} | {run['reused_test']} | {number(run['config'].get('tau_a'))} | "
            f"{number(run['config'].get('tau_r'))} | {res.get('reused_results', '—')} | "
            f"{res.get('execution_link_ms', '—')} | {run['protocol_hash']} |"
        )
    return "\n".join(lines) + "\n"


def csv_bytes(dataset: dict[str, Any]) -> bytes:
    validate(dataset)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "dataset_hash",
            "table",
            "run_or_model",
            "split",
            "slice",
            "metric",
            "value",
            "numerator",
            "denominator",
            "details",
        ]
    )

    def write(
        table: str,
        entity: str,
        split: str,
        slice: str,
        metric: str,
        value: Any,
        numerator: Any = None,
        denominator: Any = None,
        details: Any = None,
    ) -> None:
        writer.writerow(
            [
                dataset["dataset_hash"],
                table,
                entity,
                split,
                slice,
                metric,
                value,
                numerator,
                denominator,
                json.dumps(details, ensure_ascii=False) if details else "",
            ]
        )

    for model in dataset["table_2_6"]:
        for split, row in model["splits"].items():
            for name, values in row["fields"].items():
                write(
                    "2.6",
                    model["model"],
                    split,
                    "all_in_scope",
                    name,
                    values["value"],
                    values["numerator"],
                    values["denominator"],
                    values,
                )
            for name in ("invalid", "unmatched_quotes"):
                values = row[name]
                write("2.6", model["model"], split, "all_in_scope", name, **values)
            write(
                "2.6",
                model["model"],
                split,
                "all_in_scope",
                "missing_evidence",
                row["missing_evidence"],
            )
            for name, value in row["resources"].items():
                write(
                    "2.6",
                    model["model"],
                    split,
                    "all_in_scope",
                    name,
                    json.dumps(value) if isinstance(value, list) else value,
                )
    for run in dataset["runs"]:
        write(
            "manifest",
            run["variant"],
            "",
            "",
            "status",
            run["status"],
            details={
                "run_id": run["id"],
                "protocol_hash": run["protocol_hash"],
                "config": run["config"],
                "reused_test": run["reused_test"],
                "baseline_models": run["baseline_models"],
                "error": run["error"],
            },
        )
        for row in run.get("metric_rows", []):
            write(
                "2.7" if run["ablation"] == "none" and row["slice"] == "all" else "2.8",
                run["variant"],
                row["split"],
                row["slice"],
                row["metric"],
                row["value"],
                row["numerator"],
                row["denominator"],
            )
        for point in run["curves"]:
            write("2.4", run["variant"], "tune", "all", "threshold", point["tau_a"], details=point)
        for row in run["report"].get("controls", []):
            write(
                "controls",
                run["variant"],
                row["split"],
                row["case"],
                row["document"],
                row["actual"],
                details=row,
            )
        for name, value in run["report"].get("resources", {}).items():
            write(
                "resources",
                run["variant"],
                "",
                "",
                name,
                json.dumps(value) if isinstance(value, list) else value,
            )
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def png_bytes(dataset: dict[str, Any]) -> bytes:
    validate(dataset)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(10, 6), layout="constrained")
    FigureCanvasAgg(figure)
    axis = figure.subplots()
    for run in dataset["runs"]:
        points = [p for p in run["curves"] if p["error_share"] is not None]
        if not points:
            continue
        points = sorted(points, key=lambda p: (p["coverage"], p["error_share"]))
        (line,) = axis.plot(
            [p["coverage"] for p in points],
            [p["error_share"] for p in points],
            label=run["variant"],
            alpha=0.8,
        )
        chosen = next((p for p in points if p["tau_a"] == run["config"].get("tau_a")), None)
        if chosen:
            axis.scatter(
                [chosen["coverage"]],
                [chosen["error_share"]],
                color=line.get_color(),
                marker="*",
                s=130,
            )
    axis.axhline(0.02, color="grey", linestyle="--", label="Ошибка 0,02")
    axis.set(
        xlabel="Доля документов с рекомендациями (охват)",
        ylabel="Доля ошибочных связей (M > 0)",
        title="Рисунок 2.4 — настроечная часть tune; ★ выбранный τ_a",
        xlim=(-0.02, 1.02),
    )
    axis.grid(alpha=0.2)
    axis.legend(fontsize=7)
    output = io.BytesIO()
    figure.savefig(
        output,
        format="png",
        dpi=150,
        metadata={"Software": "DocLink", "Dataset": dataset["dataset_hash"]},
    )
    return output.getvalue()


def export(dataset: dict[str, Any], format: Literal["csv", "md", "png"]) -> tuple[bytes, str]:
    if format == "csv":
        return csv_bytes(dataset), "text/csv; charset=utf-8"
    if format == "md":
        return markdown(dataset).encode(), "text/markdown; charset=utf-8"
    return png_bytes(dataset), "image/png"
