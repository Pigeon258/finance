from django.urls import path

from . import views

app_name = "imports"

urlpatterns = [
    path("", views.index, name="index"),
    path("upload/", views.upload, name="upload"),
    path("<int:batch_id>/", views.detail, name="detail"),
]
