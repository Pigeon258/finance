from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from . import review, selectors, services
from .confirmation import confirm_records
from .file_safety import UnsafeImportFile
from .forms import (
    BillUploadForm,
    BulkReviewForm,
    ImportAccountRuleForm,
    MerchantCategoryRuleForm,
    RecordReviewForm,
)
from .models import ImportAccountRule, ImportBatch, MerchantCategoryRule


@require_GET
def index(request: HttpRequest):
    return render(request, "imports/index.html", {"batches": selectors.batch_list()})


@require_http_methods(["GET", "POST"])
def upload(request: HttpRequest):
    form = BillUploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            result = services.process_uploaded_bill(form.cleaned_data["bill_file"])
        except UnsafeImportFile as error:
            form.add_error("bill_file", str(error))
        else:
            if result.duplicate_file:
                messages.info(request, "该文件已经成功解析，已打开原导入批次。")
            elif result.batch.status == ImportBatch.Status.WAITING_CONFIRMATION:
                messages.success(request, "账单解析完成，记录已进入待确认区。")
            else:
                messages.error(request, "账单解析失败，原文件已删除。")
            return redirect("imports:detail", batch_id=result.batch.id)
    return render(request, "imports/upload.html", {"form": form})


@require_GET
def detail(request: HttpRequest, batch_id: int):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    page = Paginator(selectors.record_list(batch=batch), 100).get_page(request.GET.get("page"))
    available_ids = batch.records.values_list("id", flat=True)
    return render(
        request,
        "imports/detail.html",
        {
            "batch": batch,
            "page": page,
            "bulk_form": BulkReviewForm(available_ids=available_ids),
        },
    )


@require_http_methods(["GET", "POST"])
def record_review(request: HttpRequest, batch_id: int, record_id: int):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    record = selectors.record_detail(batch=batch, record_id=record_id)
    form = RecordReviewForm(request.POST or None, record=record)
    if request.method == "POST" and form.is_valid():
        try:
            review.update_record_review(
                record=record,
                mapped_account=form.cleaned_data["mapped_account"],
                selected_category=form.cleaned_data["selected_category"],
                duplicate_resolution=form.cleaned_data["duplicate_resolution"],
                selected_candidate=form.cleaned_data["selected_candidate"],
                save_merchant_rule=form.cleaned_data["save_merchant_rule"],
            )
        except (ValidationError, ValueError) as error:
            form.add_error(None, str(error))
        else:
            messages.success(request, "导入记录已更新。")
            return redirect("imports:detail", batch_id=batch.pk)
    return render(
        request, "imports/record_review.html", {"batch": batch, "record": record, "form": form}
    )


@require_http_methods(["POST"])
def batch_action(request: HttpRequest, batch_id: int):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    form = BulkReviewForm(request.POST, available_ids=batch.records.values_list("id", flat=True))
    if not form.is_valid():
        messages.error(request, "批量操作参数无效，请检查选择和操作内容。")
        return redirect("imports:detail", batch_id=batch.pk)
    data = form.cleaned_data
    try:
        if data["action"] == BulkReviewForm.Action.CONFIRM:
            result = confirm_records(batch=batch, record_ids=data["record_ids"])
            summary = (
                f"确认完成：导入 {result.imported_count} 条，"
                f"忽略 {result.ignored_count} 条，跳过 {result.skipped_count} 条。"
            )
            messages.success(request, summary)
        else:
            changed = review.bulk_update_records(
                batch=batch,
                record_ids=data["record_ids"],
                account=data["account"]
                if data["action"] == BulkReviewForm.Action.SET_ACCOUNT
                else None,
                category=data["category"]
                if data["action"] == BulkReviewForm.Action.SET_CATEGORY
                else None,
                ignore=data["action"] == BulkReviewForm.Action.IGNORE,
            )
            messages.success(request, f"已更新 {changed} 条导入记录。")
    except (ValidationError, ValueError) as error:
        messages.error(request, str(error))
    return redirect("imports:detail", batch_id=batch.pk)


@require_http_methods(["POST"])
def reapply_review(request: HttpRequest, batch_id: int):
    batch = get_object_or_404(ImportBatch, pk=batch_id)
    review.prepare_batch_for_review(batch=batch)
    messages.success(request, "已重新应用账户映射、分类规则和重复检测。")
    return redirect("imports:detail", batch_id=batch.pk)


@require_GET
def rules(request: HttpRequest):
    return render(
        request,
        "imports/rules.html",
        {
            "category_rules": selectors.merchant_category_rules(),
            "account_rules": selectors.import_account_rules(),
        },
    )


@require_http_methods(["GET", "POST"])
def category_rule_edit(request: HttpRequest, rule_id: int | None = None):
    rule = get_object_or_404(MerchantCategoryRule, pk=rule_id) if rule_id else None
    form = MerchantCategoryRuleForm(request.POST or None, instance=rule)
    if request.method == "POST" and form.is_valid():
        review.save_category_rule(form=form)
        messages.success(request, "分类推荐规则已保存。")
        return redirect("imports:rules")
    return render(request, "imports/rule_form.html", {"form": form, "title": "分类推荐规则"})


@require_http_methods(["GET", "POST"])
def account_rule_edit(request: HttpRequest, rule_id: int | None = None):
    rule = get_object_or_404(ImportAccountRule, pk=rule_id) if rule_id else None
    form = ImportAccountRuleForm(request.POST or None, instance=rule)
    if request.method == "POST" and form.is_valid():
        review.save_account_rule(form=form)
        messages.success(request, "账户映射规则已保存。")
        return redirect("imports:rules")
    return render(request, "imports/rule_form.html", {"form": form, "title": "账户映射规则"})
