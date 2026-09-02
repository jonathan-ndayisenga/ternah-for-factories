"""Branch management — owner only. A business's branches are the spine
everything else (users, inventory, sales) hangs off."""
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import redirect, render

from sales.models import InventoryLocation
from .models import Branch

owner_required = user_passes_test(lambda u: u.is_authenticated and u.role == "OWNER")


@owner_required
def branch_list(request):
    biz = request.user.business
    return render(request, "core/branches.html", {"branches": biz.branches.all().order_by("name")})


@owner_required
def branch_create(request):
    biz = request.user.business
    if request.method == "POST":
        kind = request.POST.get("kind", "OUTLET")
        branch = Branch.objects.create(
            business=biz, name=request.POST["name"].strip(), kind=kind,
            address=request.POST.get("address", ""),
        )
        # every branch gets its own inventory immediately — factory sites get the
        # finished-goods store, outlets get a POS-ready location
        InventoryLocation.objects.create(
            business=biz, branch=branch, type="PRODUCTION_STORE" if kind == "FACTORY" else "OUTLET",
        )
        return redirect("core:branches")
    return render(request, "core/branch_create.html")
