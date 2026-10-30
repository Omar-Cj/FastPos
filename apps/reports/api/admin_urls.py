from django.urls import path

from .admin_views import SalesSummaryView, WeightIncidentsView

urlpatterns = [
    path("sales-summary/", SalesSummaryView.as_view(), name="admin-report-sales-summary"),
    path("weight-incidents/", WeightIncidentsView.as_view(), name="admin-report-weight-incidents"),
]
