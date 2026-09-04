"""Branch management — owner only. A business's branches are the spine
everything else (users, inventory, sales) hangs off."""
from django.contrib.auth.decorators import user_passes_test
from django.shortcuts import get_object_or_404, redirect, render

from sales.models import InventoryLocation
from .models import Branch

owner_required = user_passes_test(lambda u: u.is_authenticated and u.role == "OWNER")


@owner_required
def branch_list(request):
    biz = request.user.business
    return render(request, "core/branches.html", {"branches": biz.branches.all().order_by("name")})


@owner_required
def business_settings(request):
    """Contact details and tagline — shown on every printed report's
    letterhead. Name stays fixed here (it's tied to the slug); everything
    else the owner can update any time, not just at onboarding."""
    biz = request.user.business
    if request.method == "POST":
        biz.contact_email = request.POST.get("contact_email", "").strip()
        biz.contact_phone = request.POST.get("contact_phone", "").strip()
        biz.address = request.POST.get("address", "").strip()
        biz.tagline = request.POST.get("tagline", "").strip()
        biz.save(update_fields=["contact_email", "contact_phone", "address", "tagline"])
        return redirect("core:business_settings")
    return render(request, "core/business_settings.html", {"business": biz})


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


@owner_required
def branch_edit(request, pk):
    """Name and address only — not type. Factory vs Outlet decides which
    kind of inventory location got created alongside it, so changing that
    after the fact would leave the branch's stock setup inconsistent;
    fixing a typo in the name doesn't need that risk."""
    biz = request.user.business
    branch = get_object_or_404(Branch, pk=pk, business=biz)
    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        if name:
            branch.name = name
            branch.address = request.POST.get("address", "").strip()
            branch.save(update_fields=["name", "address"])
            return redirect("core:branches")
        return render(request, "core/branch_edit.html", {"branch": branch, "error": "Branch name is required."})
    return render(request, "core/branch_edit.html", {"branch": branch})
