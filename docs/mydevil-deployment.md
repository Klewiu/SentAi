# Wdrożenie na MyDevil

Pliki są przygotowane lokalnie. Samo ich wgranie nie instaluje zadania Cron.

## Aplikacja

1. Utwórz środowisko virtualenv na serwerze i zainstaluj zależności projektu. Nie kopiuj `.venv` z Windows.
2. W DevilWEB ustaw stronę Python i interpreter tego środowiska. Umieść projekt w katalogu aplikacji `public_python`. Plik `passenger_wsgi.py` uruchamia konfigurację produkcyjną.
3. Utwórz serwerowy `.env`: produkcyjny klucz Django, domena HTTPS, allowed hosts, CSRF origins, baza, SMTP, Google i Stripe. Nie kopiuj lokalnych ustawień testowych jako produkcyjnych. Sekrety i prywatne faktury muszą pozostać poza katalogiem publicznych plików WWW.
4. Uruchom migracje, collectstatic i `check --deploy` interpreterem środowiska, z `--settings=sentai.settings.prod`. Sprawdź domenę i logi Passenger.
5. Skonfiguruj w Stripe webhook `https://DOMENA/stripe/webhook/` i zapisz właściwy sekret jako `STRIPE_WEBHOOK_SECRET`. Użyj tego samego trybu Stripe co ceny i klucze. Dodaj produkcyjne origin HTTPS w konfiguracji klienta Google.

## Harmonogram

Zastąp LOGIN, DOMENA oraz ścieżkę VENV rzeczywistymi wartościami. Utwórz katalog `logs` w projekcie przed dodaniem zadania (przekierowanie logu następuje przed startem Pythona).

Najpierw wykonaj ręcznie na serwerze:

```sh
/usr/home/LOGIN/VENV/bin/python /usr/home/LOGIN/domains/DOMENA/public_python/tools/run_billing_maintenance.py
```

Po poprawnym zakończeniu dodaj w DevilWEB → Zadania Cron:

```cron
*/15 * * * * /usr/home/LOGIN/VENV/bin/python /usr/home/LOGIN/domains/DOMENA/public_python/tools/run_billing_maintenance.py >> /usr/home/LOGIN/domains/DOMENA/public_python/logs/billing-maintenance.log 2>&1
```

Runner ustawia katalog roboczy i konfigurację produkcyjną. Blokada systemowa zapobiega równoległemu uruchomieniu drugiej kopii tego zadania. Błędy są zapisywane w logu, a kod zakończenia jest niezerowy. Skonfiguruj rotację logu i monitorowanie błędów na hostingu.

Po pierwszym zaplanowanym wykonaniu sprawdź log: powinien zawierać zakończenie oraz `0 subscription failures`. Przetestuj webhook w trybie testowym Stripe, zapis płatności, dostęp klienta i powiadomienia. Dopiero taki test potwierdza działanie na serwerze.

Dokumentacja dostawcy:
- https://pomoc.mydevil.net/Django/
- https://pomoc.mydevil.net/Python/
- https://pomoc.mydevil.net/Cron/
