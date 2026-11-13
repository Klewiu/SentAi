# Wdrożenie na MyDevil

Pliki są przygotowane lokalnie. Samo ich wgranie nie instaluje zadania Cron.

Produkcja używa SMTP MyDevil (`mail92.mydevil.net`, port `587`, STARTTLS) i nadawcy `kontakt@xoaila.pl`. W produkcyjnym środowisku aplikacji ustaw (wartości z `.env.example` dla lokalnego developmentu trzeba nadpisać):

```dotenv
EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=mail92.mydevil.net
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_USE_SSL=False
EMAIL_HOST_USER=kontakt@xoaila.pl
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=kontakt@xoaila.pl
REGISTRATION_EMAIL_VERIFICATION_REQUIRED=True
```

Wartość `EMAIL_HOST_PASSWORD` wpisz bezpośrednio w ustawieniach środowiska serwera; nie umieszczaj hasła w repozytorium ani w wiadomości. Produkcyjne ustawienie `REGISTRATION_EMAIL_VERIFICATION_REQUIRED` domyślnie włącza potwierdzenie adresu; rejestracja nie aktywuje konta, dopóki użytkownik nie kliknie linku z wiadomości. Port i szyfrowanie można zmienić przez `EMAIL_PORT`, `EMAIL_USE_TLS` i `EMAIL_USE_SSL`.

### Lokalny test SMTP

W lokalnym, ignorowanym przez Git pliku `.env` zastąp wartości `EMAIL_*` i `DEFAULT_FROM_EMAIL` zmiennymi SMTP z przykładu powyżej (nie dopisuj drugich wpisów tych samych kluczy) i ustaw:

```dotenv
SITE_BASE_URL=http://127.0.0.1:8000
REGISTRATION_EMAIL_VERIFICATION_REQUIRED=True
```

Wpisz prawdziwe hasło skrzynki wyłącznie w lokalnym `.env`. Uruchom aplikację ponownie po zmianie `.env`; wiadomość testowa będzie wysyłana przez MyDevil do adresu podanego w formularzu. Link potwierdzający będzie prowadził do lokalnego serwera, więc kliknij go w przeglądarce, w której lokalna aplikacja działa.

Wiadomość potwierdzająca korzysta z szablonów `templates/emails/verification_email.html` i `templates/emails/verification_email.txt`. Język wybierany jest na podstawie języka formularza. Nagłówek wiadomości używa tego samego wordmarku co menu aplikacji: `xoaila` z zieloną kropką.

Jeśli `31.186.84.37` jest przypisanym w DevilWEB wychodzącym adresem IP poczty, dodaj go również do rekordu SPF domeny, zachowując istniejące mechanizmy, na przykład:

```text
v=spf1 mx a ip4:31.186.84.37/32 include:mail92.mydevil.net -all
```

Włącz też DKIM w DevilWEB i opublikuj wygenerowany rekord DNS. Szczegóły: [dokumentacja poczty MyDevil](https://pomoc.mydevil.net/Poczta/).

## Aplikacja

1. Utwórz środowisko virtualenv na serwerze i zainstaluj zależności projektu. Nie kopiuj `.venv` z Windows.
2. W DevilWEB ustaw stronę Python i interpreter tego środowiska. Umieść projekt w katalogu aplikacji `public_python`. Plik `passenger_wsgi.py` uruchamia konfigurację produkcyjną.
3. Utwórz serwerowy `.env`: produkcyjny klucz Django, domena HTTPS, allowed hosts, CSRF origins, baza, SMTP, Google i Stripe. Nie kopiuj lokalnych ustawień testowych jako produkcyjnych. Sekrety i prywatne faktury muszą pozostać poza katalogiem publicznych plików WWW.
4. Uruchom migracje, collectstatic i `check --deploy` interpreterem środowiska, z `--settings=sentai.settings.prod`. Sprawdź domenę i logi Passenger.
5. Skonfiguruj w Stripe webhook `https://DOMENA/stripe/webhook/` i zapisz właściwy sekret jako `STRIPE_WEBHOOK_SECRET`. Użyj tego samego trybu Stripe co ceny i klucze. Dodaj produkcyjne origin HTTPS w konfiguracji klienta Google.

Domyślne dane przelewu PLN to Wicky Wave Sp. z o.o., ul. Ezopa 57, 04-805 Warszawa, NIP 9522290935, IBAN `PL79 1160 2202 0000 0007 3928 4688`. Zmienne środowiskowe `MANUAL_PAYMENT_RECIPIENT`, `MANUAL_PAYMENT_ADDRESS`, `MANUAL_PAYMENT_TAX_ID`, `MANUAL_PAYMENT_IBAN_PLN` i `MANUAL_PAYMENT_BANK` nadpisują te wartości: sprawdź istniejący serwerowy `.env` i usuń lub popraw stare dane demonstracyjne. Przelewy EUR wymagają osobnego ustawienia `MANUAL_PAYMENT_IBAN_EUR`.

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

### Analityka crawlerów

Zbieranie żądań plików publicznych jest domyślnie wyłączone dla każdej strony. Administrator może je włączyć w raporcie firmy. Zdarzenia trafiają do kolejki w bazie, a harmonogram Cron agreguje je do dziennych statystyk; aplikacja nie wymaga Redis ani Celery. Zaplanuj osobne uruchomienie co minutę:

```cron
* * * * * /usr/home/LOGIN/VENV/bin/python /usr/home/LOGIN/domains/DOMENA/public_python/tools/run_crawler_analytics.py >> /usr/home/LOGIN/domains/DOMENA/public_python/logs/crawler-analytics.log 2>&1
```

Uruchom skrypt ręcznie po migracji, aby sprawdzić konfigurację. Każde uruchomienie przetwarza maksymalnie 2000 zdarzeń; blokada systemowa zapobiega nakładaniu się kopii. Kolejka deduplikuje żądania tej samej strony, IP, bota i pliku w tej samej minucie. Surowe adresy IP nie są zapisywane: do czasu agregacji przechowywany jest jedynie ich HMAC, po czym zdarzenia kolejki są usuwane. Skonfiguruj rotację logu i monitorowanie błędów analogicznie do zadania rozliczeniowego.

Dokumentacja dostawcy:
- https://pomoc.mydevil.net/Django/
- https://pomoc.mydevil.net/Python/
- https://pomoc.mydevil.net/Cron/
