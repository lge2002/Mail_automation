from django.urls import path
from . import views # Import views from the current app

urlpatterns = [
    # Your existing URL for the live log/main import page
    path('', views.import_emails_view, name='import_emails_view'),
    
    # NEW URL for Historical Logs
    path('logs/', views.historical_logs_view, name='historical_logs'),
]
