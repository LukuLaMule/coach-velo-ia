"""Controles statiques du script d'une page generee.

Ne remplace pas un moteur JS, mais attrape les deux fautes qui ont deja
casse la page : delimiteurs desequilibres, et corps de fonction flechee
concis contenant une instruction (`()=> const x = 1; f();`), qui empeche
tout le script de se parser — donc y compris les try/catch de secours.
"""
import re, sys

MOTS = ("const ", "let ", "var ", "if ", "if(", "for ", "for(", "while ",
        "while(", "return ", "switch", "throw ")

def decoupe(js):
    """Renvoie le script avec chaines, gabarits et commentaires neutralises."""
    out = []; i = 0; n = len(js)
    while i < n:
        c = js[i]
        if c in "\"'":
            q = c; out.append(" "); i += 1
            while i < n and js[i] != q:
                out.append("\n" if js[i] == "\n" else " ")
                if js[i] == "\\": out.append(" "); i += 1
                i += 1
            out.append(" "); i += 1; continue
        if c == "`":
            out.append(" "); i += 1; d = 0
            while i < n:
                if js[i] == "\\": out.append("  "); i += 2; continue
                if js[i] == "$" and i+1 < n and js[i+1] == "{": d += 1; out.append("  "); i += 2; continue
                if js[i] == "}" and d: d -= 1; out.append(" "); i += 1; continue
                if js[i] == "`" and not d: break
                out.append("\n" if js[i] == "\n" else " "); i += 1
            out.append(" "); i += 1; continue
        if c == "/" and i+1 < n and js[i+1] == "/":
            while i < n and js[i] != "\n": out.append(" "); i += 1
            continue
        if c == "/" and i+1 < n and js[i+1] == "*":
            j = js.find("*/", i) + 2
            out.append("".join("\n" if ch == "\n" else " " for ch in js[i:j])); i = j; continue
        out.append(c); i += 1
    return "".join(out)

def controle(js, nom):
    net = decoupe(js)
    pbs = []
    # 1) equilibre des delimiteurs
    pile = []; PAIRS = {")": "(", "]": "[", "}": "{"}
    for i, c in enumerate(net):
        if c in "([{": pile.append((c, net.count("\n", 0, i)+1))
        elif c in ")]}":
            if not pile or pile[-1][0] != PAIRS[c]:
                pbs.append(f"ligne {net.count(chr(10),0,i)+1} : '{c}' inattendu"); break
            pile.pop()
    if pile: pbs.append(f"non ferme : {pile[:3]}")
    # 2) corps de fonction flechee concis contenant une instruction
    # profondeur de parentheses en chaque point : un ';' n'est fautif que si la
    # flechee est un ARGUMENT d'appel — sinon il termine simplement la
    # declaration qui la contient (const f = x => expr;), ce qui est legal.
    dedans = []; pile_d = []
    for c in net:
        dedans.append(pile_d[-1] if pile_d else "")
        if c in "([{": pile_d.append(c)
        elif c in ")]}" and pile_d: pile_d.pop()
    for m in re.finditer(r"(?:\)|[A-Za-z_$][\w$]*)\s*=>", net):
        j = m.end()
        while j < len(net) and net[j] in " \n\t": j += 1
        if j < len(net) and net[j] == "{": continue          # corps a accolades : rien a verifier
        dans_appel = j < len(net) and dedans[j] == "("
        prof = 0; k = j; corps = []
        while k < len(net):
            c = net[k]
            if c in "([{": prof += 1
            elif c in ")]}":
                if prof == 0: break
                prof -= 1
            elif c == ";" and prof == 0:
                if dans_appel:
                    pbs.append(f"ligne {net.count(chr(10),0,k)+1} : '; ' dans un corps de flechee sans accolades, passe en argument")
                break
            corps.append(c); k += 1
        tete = "".join(corps).lstrip()
        if any(tete.startswith(w) for w in MOTS):
            pbs.append(f"ligne {net.count(chr(10),0,j)+1} : instruction '{tete[:12].strip()}' en corps de flechee sans accolades")
    print(f"{nom} : {'OK' if not pbs else 'PROBLEMES'}")
    for p in pbs: print("   -", p)
    return not pbs

if __name__ == "__main__":
    ok = True
    for f in sys.argv[1:]:
        h = open(f, encoding="utf-8").read()
        for bloc in h.split("<script>")[1:]:
            ok &= controle(bloc.split("</script>")[0], f)
    sys.exit(0 if ok else 1)
