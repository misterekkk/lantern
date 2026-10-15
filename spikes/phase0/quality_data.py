"""Phase 0 spike: synthetic speech corpus and queries for the audio quality test.

Recordings are generated with macOS `say` (Polish voice Zosia, English voice Daniel).
Queries paraphrase the content on purpose (no verbatim phrases),
plus a few exact-term and cross-lingual queries.
"""

RECORDINGS = {
    "pl_budzet": ("pl", (
        "Dzień dobry wszystkim, zaczynamy cotygodniowe spotkanie działu. Najważniejszy temat to budżet na "
        "kolejny kwartał. Zarząd poprosił nas o ograniczenie kosztów o mniej więcej piętnaście procent. "
        "Proponuję, żebyśmy w pierwszej kolejności zmniejszyli wydatki na kampanie reklamowe w mediach "
        "społecznościowych, bo ich skuteczność w ostatnich miesiącach była słaba. Druga sprawa to zatrudnienie. "
        "Do końca roku nie otwieramy nowych etatów, a dwa wakaty, które mieliśmy obsadzić w listopadzie, "
        "przesuwamy na wiosnę. Księgowość prosi też o wyjaśnienie faktury numer cztery tysiące czterysta "
        "siedemdziesiąt jeden od agencji kreatywnej, bo kwota nie zgadza się z umową. Na koniec przypominam, "
        "że raporty kwartalne trzeba oddać do piątku, a prezentacja dla zarządu odbędzie się w przyszły wtorek. "
        "Jeśli ktoś ma pomysły na dodatkowe oszczędności, proszę przesłać je mailem do środy."
    )),
    "pl_bigos": ("pl", (
        "Dzisiaj pokażę wam, jak przygotować prawdziwy bigos myśliwski. Potrzebujemy kilograma kiszonej kapusty, "
        "pół kilograma świeżej kapusty, łopatki wieprzowej, dobrej kiełbasy i garści suszonych grzybów. "
        "Grzyby namaczamy wieczorem dzień wcześniej. Kiszoną kapustę płuczemy tylko lekko, żeby nie straciła "
        "smaku, a potem dusimy ją razem z poszatkowaną świeżą kapustą. Mięso kroimy w kostkę i obsmażamy "
        "na smalcu, dopiero potem dodajemy je do garnka. Do tego liść laurowy, ziele angielskie, jałowiec "
        "i odrobina suszonych śliwek, które nadają charakterystyczną słodycz. Bigos gotujemy na małym ogniu "
        "przez kilka godzin, a najlepiej odgrzewać go przez trzy dni, bo z każdym dniem jest smaczniejszy. "
        "Na koniec można dodać kieliszek czerwonego wina i łyżkę koncentratu pomidorowego."
    )),
    "pl_awaria": ("pl", (
        "Podsumowanie wczorajszego incydentu. Około drugiej w nocy główny serwer bazy danych przestał "
        "odpowiadać. Monitoring wysłał alert, ale dyżurny zobaczył go dopiero po czterdziestu minutach, "
        "bo powiadomienia trafiały na stary numer telefonu. Przyczyną był zapełniony dysk z logami transakcji. "
        "W trakcie próby odzyskania danych pojawił się błąd ORA zero jeden pięć pięć pięć, czyli zbyt stary "
        "snapshot. Ostatecznie przywróciliśmy bazę z kopii zapasowej z północy i odtworzyliśmy brakujące "
        "transakcje z dziennika. Łącznie system był niedostępny przez trzy godziny. Wnioski: musimy dodać "
        "alert na zajętość dysku przy osiemdziesięciu procentach, zaktualizować listę dyżurów i co miesiąc "
        "testować odtwarzanie kopii zapasowych na środowisku testowym. Pełny raport poawaryjny będzie "
        "gotowy w czwartek."
    )),
    "pl_tatry": ("pl", (
        "W sierpniu pojechaliśmy na tydzień do Zakopanego. Pierwszego dnia padał deszcz, więc zwiedzaliśmy "
        "Krupówki i muzeum tatrzańskie. Drugiego dnia pogoda się poprawiła i wyruszyliśmy wcześnie rano "
        "z Palenicy Białczańskiej asfaltową drogą w stronę Morskiego Oka. Po około dwóch godzinach marszu "
        "dotarliśmy do schroniska, a widok na jezioro i otaczające je szczyty był niesamowity. Najodważniejsi "
        "poszli dalej nad Czarny Staw pod Rysami. Kolejne dni spędziliśmy na Gubałówce i w Dolinie "
        "Kościeliskiej, gdzie weszliśmy do jaskini Mroźnej. Wieczorami jedliśmy oscypki z żurawiną i kwaśnicę. "
        "Rada na przyszłość: bilety do parku narodowego najlepiej kupić przez internet, a parking zarezerwować "
        "kilka dni wcześniej, bo w sezonie miejsc brakuje już o szóstej rano."
    )),
    "pl_lekarz": ("pl", (
        "Byłam dzisiaj u ortopedy z powodu bólu w dolnej części pleców, który dokucza mi od kilku tygodni. "
        "Lekarz zbadał mnie i stwierdził, że to najprawdopodobniej przeciążenie mięśni przykręgosłupowych, "
        "wynikające z długiego siedzenia przy komputerze. Zlecił rezonans, ale bez pilnego trybu. Na razie "
        "dostałam skierowanie na dziesięć zabiegów rehabilitacji. Fizjoterapeutka pokazała mi zestaw ćwiczeń "
        "na wzmocnienie mięśni brzucha i rozciąganie, które mam robić codziennie rano przez piętnaście minut. "
        "Polecono mi też zmianę krzesła w pracy, biurko z regulacją wysokości i przerwy na spacer co godzinę. "
        "Jeśli ból będzie promieniował do nogi albo pojawi się drętwienie, mam od razu wrócić na kontrolę. "
        "Następna wizyta za sześć tygodni."
    )),
    "pl_aplikacja": ("pl", (
        "Omówmy plan wydania nowej wersji naszej aplikacji mobilnej. Sprint kończy się w przyszły piątek, "
        "a do tego czasu musimy zamknąć wszystkie zgłoszenia oznaczone jako krytyczne. Wersja na Androida "
        "trafi najpierw do grupy testerów zamkniętych, a po tygodniu do wszystkich użytkowników. W przypadku "
        "iPhone'a pamiętajcie, że weryfikacja w sklepie Apple może potrwać kilka dni, a ostatnio odrzucili "
        "nam aktualizację z powodu braku opisu, do czego używamy lokalizacji. Dlatego teksty z prośbą "
        "o uprawnienia trzeba poprawić jeszcze w tym tygodniu. Zespół marketingu przygotuje zrzuty ekranu "
        "i notatkę o nowościach. Po premierze przez pierwsze dwa dni monitorujemy raporty awarii i oceny "
        "w sklepach, a dyżurny programista ma być dostępny także w weekend."
    )),
    "pl_kredyt": ("pl", (
        "Zastanawiam się nad refinansowaniem kredytu hipotecznego. Kiedy braliśmy kredyt cztery lata temu, "
        "oprocentowanie było zmienne i rata wynosiła około dwóch tysięcy złotych. Po podwyżkach stóp "
        "procentowych przez bank centralny rata wzrosła prawie o połowę. Teraz kilka banków oferuje "
        "oprocentowanie stałe na pięć lat, które jest niższe niż nasze obecne. Doradca wyliczył, że po "
        "przeniesieniu kredytu zaoszczędzilibyśmy kilkaset złotych miesięcznie, ale trzeba doliczyć koszt "
        "nowej wyceny mieszkania, prowizję i ubezpieczenie. Warto też sprawdzić, czy obecny bank nie zgodzi "
        "się obniżyć marży, jeśli pokażemy mu konkurencyjną ofertę. Decyzję chcemy podjąć przed końcem "
        "miesiąca, bo oferta promocyjna jest ograniczona czasowo."
    )),
    "pl_fotosynteza": ("pl", (
        "Na dzisiejszym wykładzie zajmiemy się fotosyntezą, czyli procesem, w którym rośliny zamieniają "
        "energię światła słonecznego w energię chemiczną. Proces zachodzi w chloroplastach, a kluczową rolę "
        "odgrywa zielony barwnik, chlorofil. W fazie jasnej, zależnej od światła, woda jest rozkładana, "
        "uwalnia się tlen, a powstają nośniki energii, czyli ATP i NADPH. W fazie ciemnej, nazywanej cyklem "
        "Calvina, dwutlenek węgla z powietrza jest wiązany i przekształcany w cukry, przede wszystkim glukozę. "
        "Intensywność fotosyntezy zależy od natężenia światła, stężenia dwutlenku węgla i temperatury. "
        "Na następnych zajęciach porównamy rośliny typu C3, C4 i CAM, które przystosowały się do suchego "
        "i gorącego klimatu."
    )),
    "en_demo": ("en", (
        "Welcome to the product demo. Today I'm going to show you the new search feature in our developer "
        "tools. Instead of matching exact keywords, it understands what you mean. For example, if I type "
        "find the function that retries failed network requests, it finds the right helper even though the "
        "function is called backoff wrapper and never mentions the word retry. Under the hood, every function "
        "and class in your repository is turned into a vector, and we combine that with classic keyword "
        "search so that exact error messages still match perfectly. Everything runs locally on your laptop, "
        "so your source code never leaves the machine. Indexing a medium sized repository takes a few "
        "minutes, and after that, results show up while you type."
    )),
    "en_climate": ("en", (
        "In this episode we talk about heat waves in big cities. Urban areas can be several degrees warmer "
        "than the surrounding countryside, an effect known as the urban heat island. Asphalt and concrete "
        "absorb sunlight during the day and release the heat at night, so the city never really cools down. "
        "One of the cheapest and most effective solutions is planting trees. Their shade lowers surface "
        "temperatures dramatically, and evaporation from leaves cools the air. Some cities are also painting "
        "roofs white, building green roofs, and opening public cooling centers for elderly residents. "
        "Our guest, an urban planner from Barcelona, explains how her city created shaded walking corridors "
        "connecting schools and parks."
    )),
    "en_interview": ("en", (
        "Thanks for joining the interview. Could you tell us about your recent experience? Sure. For the last "
        "three years I've been working as a platform engineer at a logistics company. My main responsibility "
        "was running our Kubernetes clusters on three cloud regions. I migrated about forty services from "
        "virtual machines to containers, wrote Helm charts, and set up autoscaling based on queue length. "
        "I also introduced a GitOps workflow with Argo CD, so every change to the cluster goes through a pull "
        "request. The hardest incident I handled was a certificate expiry that took down the ingress "
        "controller. After that, we automated certificate renewal and added alerts thirty days before "
        "expiry. I'm now looking for a role with more focus on developer experience."
    )),
    "en_party": ("en", (
        "Hi everyone, quick family call about Grandma's ninetieth birthday next month. We want to keep it a "
        "surprise, so please don't mention anything when you talk to her. Aunt Mary has offered her garden "
        "for the party, and we'll rent a big tent in case it rains. Uncle Tom will bake his famous apple pie, "
        "and the kids are preparing a slideshow with old photos from the family album. We still need someone "
        "to pick up Grandma and bring her over without her suspecting anything, maybe under the pretext of "
        "a short walk. Please send me your confirmation by Friday so I can order enough food, and let me know "
        "about any allergies."
    )),
}

# Long recording: three separate segments; tests >30 s truncation and windowing.
LONG_SEGMENTS = [
    ("samochod", (
        "Wczoraj odebrałem samochód z warsztatu. Mechanik wymienił klocki hamulcowe i tarcze na przedniej osi, "
        "bo przy hamowaniu słychać było metaliczny pisk, a kierownica lekko drgała. Okazało się też, że płyn "
        "hamulcowy był bardzo stary i zawierał za dużo wody, więc wymienili go w całości. Przy okazji zrobili "
        "przegląd zawieszenia i znaleźli luz na końcówce drążka kierowniczego, którą trzeba będzie wymienić "
        "w przyszłym miesiącu. Zapłaciłem za wszystko około tysiąca dwustu złotych, łącznie z robocizną. "
        "Mechanik radzi, żeby przed zimą sprawdzić jeszcze akumulator, bo ma już sześć lat, i założyć opony "
        "zimowe najpóźniej na początku listopada. Mówił też, że warto co dwa lata wymieniać płyn hamulcowy, "
        "bo to tani zabieg, a bardzo wpływa na bezpieczeństwo. Na koniec umyli auto, co było miłym gestem. "
        "Przy następnej wizycie zrobię też wymianę oleju i filtrów, bo zbliża się termin z książki serwisowej."
    )),
    ("mecz", (
        "Teraz kilka słów o sobotnim meczu naszej drużyny piłkarskiej. Graliśmy na wyjeździe z liderem tabeli "
        "i mało kto dawał nam szanse. Pierwsza połowa była bardzo wyrównana, ale w trzydziestej minucie "
        "straciliśmy gola po rzucie rożnym, bo nikt nie upilnował wysokiego obrońcy. Po przerwie trener zmienił "
        "ustawienie na trzech obrońców i wpuścił dwóch szybkich skrzydłowych. To zadziałało. W sześćdziesiątej "
        "piątej minucie wyrównaliśmy po pięknym strzale z dystansu, a w doliczonym czasie gry nasz napastnik "
        "wykorzystał rzut karny. Ostatecznie wygraliśmy dwa do jednego, co jest największą niespodzianką "
        "tej rundy. Kibice, którzy przyjechali autokarem, śpiewali do samego końca. W przyszłym tygodniu gramy "
        "u siebie i bilety są już prawie wyprzedane, więc kto chce przyjść, niech się pospieszy."
    )),
    ("ogrod", (
        "Na koniec opowiem, co działo się w tym tygodniu w ogrodzie. W końcu zrobiło się na tyle ciepło, że "
        "mogłam przesadzić sadzonki pomidorów z parapetu do szklarni. Wybrałam trzy odmiany: malinowe, "
        "koktajlowe i bawole serce. Każdą roślinę przywiązałam do drewnianego palika, a ziemię wymieszałam "
        "z kompostem. Między pomidorami posadziłam bazylię i aksamitki, bo podobno odstraszają szkodniki. "
        "Na grządce obok wysiałam marchew, rzodkiewkę i koper, a przy płocie posadziłam fasolę tyczną. "
        "Największy problem w tym roku to ślimaki, które w jedną noc zjadły całą sałatę, więc rozsypałam "
        "wokół grządek popiół i skorupki jaj. Podlewam rano deszczówką ze zbiornika, a co dwa tygodnie "
        "zasilam rośliny gnojówką z pokrzywy. Jeśli pogoda dopisze, pierwsze pomidory będą w lipcu."
    )),
]

# (query, target recording id, optional long-recording segment id)
QUERIES = [
    ("cięcia w wydatkach na reklamę w internecie", "pl_budzet", None),
    ("wstrzymanie rekrutacji nowych pracowników", "pl_budzet", None),
    ("faktura 4471", "pl_budzet", None),
    ("jak ugotować kapustę z mięsem i grzybami", "pl_bigos", None),
    ("awaria bazy danych w nocy i odtwarzanie backupu", "pl_awaria", None),
    ("błąd ORA-01555", "pl_awaria", None),
    ("wycieczka w góry nad jezioro w Tatrach", "pl_tatry", None),
    ("ból kręgosłupa i ćwiczenia z fizjoterapeutą", "pl_lekarz", None),
    ("publikacja aplikacji na iOS i odrzucenie przez App Store", "pl_aplikacja", None),
    ("rata kredytu na mieszkanie po wzroście stóp", "pl_kredyt", None),
    ("jak rośliny wytwarzają cukier dzięki światłu", "pl_fotosynteza", None),
    ("semantic code search running locally", "en_demo", None),
    ("how trees cool down cities in summer", "en_climate", None),
    ("candidate experience with container orchestration", "en_interview", None),
    ("surprise party for grandmother", "en_party", None),
    # cross-lingual
    ("rozmowa rekrutacyjna inżyniera platformy Kubernetes", "en_interview", None),
    ("urodziny babci w ogrodzie cioci", "en_party", None),
    ("database outage postmortem", "pl_awaria", None),
    ("mortgage refinancing fixed interest rate", "pl_kredyt", None),
    # long recording, segments at different offsets
    ("wymiana klocków hamulcowych w warsztacie", "pl_dlugie", "samochod"),
    ("wygrana drużyny po rzucie karnym", "pl_dlugie", "mecz"),
    ("sadzenie pomidorów w szklarni i ślimaki", "pl_dlugie", "ogrod"),
]
