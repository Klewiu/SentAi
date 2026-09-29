# Cennik Stripe

Stripe jest źródłem kwoty i parametrów ceny. Aplikacja przechowuje zweryfikowane Price ID w lokalnej bazie, aby tworzyć Checkout bez odpytywania Stripe przy każdym wyświetleniu strony.

## Dodawanie i zmiana ceny

1. W Stripe Dashboard otwórz **Product catalog**, wybierz produkt i utwórz aktywną cenę cykliczną rozliczaną raz w roku.
2. Skopiuj **Price ID** zaczynające się od `price_`. Nie kopiuj Product ID zaczynającego się od `prod_`.
3. W panelu administratora aplikacji otwórz zarządzanie cenami, wybierz plan i wklej Price ID.
4. Aplikacja pobierze cenę ze Stripe i sprawdzi jej kwotę, walutę, aktywność oraz roczny okres rozliczenia.

Kwota i waluta nie są wpisywane ręcznie w aplikacji. Jeśli dla danego planu i waluty była już aktywna cena, zostanie lokalnie zarchiwizowana. Istniejące subskrypcje nadal wskazują poprzedni Price ID, a nowa cena jest używana przy nowych zakupach i przyszłych zmianach planu.

Archiwizacja w aplikacji nie zmienia ceny w Stripe. Ponowna aktywacja najpierw sprawdza w Stripe, czy cena nadal istnieje, jest aktywna i ma zgodne parametry.

## Inna instalacja

Każda instalacja ma osobną lokalną bazę. W panelu tej instalacji należy zaimportować te same Price ID, używając klucza API do właściwego konta i trybu Stripe. Cena z trybu testowego nie jest dostępna przez klucz produkcyjny i odwrotnie.

Zmienne `.env` z kwotami i Price ID nie są używane. Wymagane pozostają klucz API Stripe oraz sekret webhooka odpowiedni dla danego środowiska.
