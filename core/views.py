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
