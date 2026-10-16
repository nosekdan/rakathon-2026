- vime, kdo actually (anonymne) sel do studie, tak to muzem pred porotou demonstrovat - ! - v tom seznamu je i nekdo, kdo byl po nejakym additional screeningu vyhozen ze studie, tak muzem vyzkouset, jak si s tim poradime
- mame notifikaci u karty pacienta, co moc nerusi (ukazuje se jen praktickemu lekari, at to nespamuje ostatni), zaroven vedlejsi okno, kde je vic detailu, kolik splnilo podminek a kolik ne a proc (ukladame v db i zdroj priznaku)
- pokud by casem chtela nemocnice mit ulozeno vic informaci o pacientech, tak muze v ty nasi karte kliknout pridat (ulozit) a z naseho systemu se to propoji i do jejich, do ktereho momentalne nesaheme, odtamtud jen berem data
- flow kdyz chybi data - casto - pokud bychom chteli byt fancy, muzem zkusit navrhnout nejaky 
- llm nikdy nema vyloucit pacienta ze studie - asi nejaky potvrzeni doktora, pokud nevis, tak ho spis navrhni, at si to pak sam 
- kdyz specificky kriterium studie chybi, tak prohledat nevylouceny pacienty (naivni reseni, kdyztak vyzkouset, jak to jde)
- kdyz pridavame studii, musi potvrdit lekar nase extrahovany data a studie se verifikuje, lekar muze upravovat
- brat cas jako kriterium, kde vime, ulozit datum, at muzem dopocitat
- notifikace jen pri zmene (nova studie, pacientovi se neco stalo, tak prisel; nebo vyprsel nejakej casovej limit (tehotensvtvi vyloucilo atd))
- ukladat leky, co kdo bral, at tim muzem odfiltrovavat studie, ktere to vylucuje


poznamky pro pitch:
    - ukazujem lekari primo kde v dokumentu to je, ne jen dokument
    - strukturovana data porovnavame deterministicky, pro lepsi presnost a auditelnost
    - inkrementalni zpracovani - kdyz prijdou novy informace o pacientovi, tak berem jen novej report, ne nic staryho 
    - zatim nabizime jen mistni studie, casem muzem pridat studie ve stejne zemi. pokud chce pacient neco vic, tak je to na dukladny studovani, ne takhle simple workflow
    - data o pacientech berem z mistniho nisu, ktere si transformujem do lokalni databaze, nikdy necestuje mimo, porovnavame jen zmeny, takze llm volame zridka (v radu stovek dotazu za den), takze levny

