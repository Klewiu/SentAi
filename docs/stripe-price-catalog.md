# Cennik Stripe

Stripe jest źródłem aktualnych cen. Każda instalacja aplikacji przechowuje lokalną kopię; nie trzeba kopiować bazy klientów ani zmiennych `STRIPE_*_PRICE_ID` i `STRIPE_*_PRICE_AMOUNT`.

## Pierwsza konfiguracja istniejącego konta

Na instalacji, która ma poprawne dotychczasowe ceny, uruchom jednorazowo:

```
python manage.py sync_stripe_prices --link-existing
```

Polecenie sprawdza zgodność kwot z API Stripe i przypisuje istniejącym cenom stałe lookup keys. Nie zmienia kwot ani subskrypcji. Istniejące aktualne klucze w Stripe mają pierwszeństwo.

Klucze: `xoaila_basic_pln_year`, `xoaila_basic_eur_year`, `xoaila_plus_pln_year`, `xoaila_plus_eur_year`, `xoaila_pro_pln_year`, `xoaila_pro_eur_year`.

## Drugi komputer i wdrożenie

1. Skonfiguruj `STRIPE_SECRET_KEY` dla tego samego konta Stripe i tego samego trybu (test/live).
2. W panelu administratora otwórz ceny i kliknij **Synchronizuj ceny ze Stripe** albo uruchom `python manage.py sync_stripe_prices`.
3. Wynik pełnej konfiguracji: 6/6 aktywnych cen. Osobne bazy klientów pozostają niezależne.

`maintain_billing` także synchronizuje cennik; po wdrożeniu uruchamiaj je z harmonogramu. Strony publiczne korzystają z bazy i nie odpytują Stripe przy każdym wejściu. Zmiany na drugim komputerze są widoczne po jego synchronizacji.

## Zmiana ceny w panelu

Wybierz plan, walutę i nową roczną kwotę w złotych/euro. Pozostaw opcjonalny identyfikator pusty. Aplikacja utworzy cenę w Stripe i przeniesie na nią lookup key. Dotychczasowy rekord zostaje w historii. Subskrypcje nie są modyfikowane: obecni klienci odnawiają po dotychczasowej cenie.

Opcjonalny identyfikator `price_...` służy do powiązania ceny już istniejącej w Stripe; wpisana kwota musi się z nią zgadzać. Nowy cennik zastępuje ofertę dla nowych zakupów, w tym przyszłych zmian planu.

Archiwizacja i aktywacja działają w Stripe, więc dotyczą wszystkich instalacji po synchronizacji. Nie wykonuj próbnych podwyżek na koncie produkcyjnym. Po utracie połączenia najpierw synchronizuj: Stripe mógł zakończyć operację mimo braku odpowiedzi. Błąd odczytu pozostawia poprzednią lokalną kopię.

Stare ustawienia cen w `.env` nie są już używane do wyboru oferty. Można je usunąć. Klucze API oraz sekret webhooka nadal są wymagane dla odpowiednich funkcji płatności.
