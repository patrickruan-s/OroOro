from django.contrib import admin
from django.urls import path
from spotify import views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('spotify-login/', views.spotify_login, name='spotify_login'),
    path('callback/', views.spotify_callback, name='spotify_callback'),
    path('logout/', views.logout_view, name='logout'),
    path('profile/', views.profile, name='profile'),
    path('artist/<str:spotify_id>/', views.artist_detail, name='artist_detail'),
    path('api/heatmap/', views.heatmap_data, name='heatmap_data'),
    path('loading/', views.loading, name='loading'),
    path('api/start-sync/', views.start_sync, name='start_sync'),
    path('api/sync-status/', views.sync_status, name='sync_status'),
]
