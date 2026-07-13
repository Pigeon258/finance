from django.contrib import messages
from django.core.paginator import Paginator
from django.http import HttpRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from . import selectors, services
from .file_safety import UnsafeImportFile
from .forms import BillUploadForm
from .models import ImportBatch


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
    return render(request, "imports/detail.html", {"batch": batch, "page": page})
