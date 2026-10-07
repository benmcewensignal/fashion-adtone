"""Contrastive Style Descriptors (Somepalli et al., "Measuring Style Similarity in Diffusion Models",
ECCV 2024): a CLIP ViT-L/14 backbone with a style projection trained to capture style apart from
content. The model definition is taken from https://github.com/learn2phoenix/CSD (MIT licence),
reduced to what inference needs; the weights are tomg-group-umd/CSD-ViT-L on the Hugging Face hub.
"""
import copy

import clip
import torch
import torch.nn as nn


def _float(module: nn.Module) -> None:
    """CLIP loads in half precision on GPU; the descriptors are computed in float32."""
    for m in module.modules():
        for name in ("weight", "bias", "in_proj_weight", "in_proj_bias"):
            t = getattr(m, name, None)
            if isinstance(t, torch.Tensor) and t.dtype == torch.float16:
                t.data = t.data.float()
    for name in ("class_embedding", "positional_embedding", "proj"):
        t = getattr(module, name, None)
        if isinstance(t, torch.Tensor) and t.dtype == torch.float16:
            t.data = t.data.float()


class CSD_CLIP(nn.Module):
    """Backbone and the two projection heads; forward returns (features, content, style), the last two
    unit length."""

    def __init__(self, name: str = "vit_large", content_proj_head: str = "default"):
        super().__init__()
        if name != "vit_large":
            raise ValueError("only the ViT-L model is published")
        clipmodel, _ = clip.load("ViT-L/14", device="cpu", jit=False)
        self.backbone = clipmodel.visual
        self.embedding_dim = 1024
        _float(self.backbone)
        self.last_layer_style = copy.deepcopy(self.backbone.proj)
        self.last_layer_content = copy.deepcopy(self.backbone.proj)
        self.backbone.proj = None

    def forward(self, x):
        feature = self.backbone(x)
        style = nn.functional.normalize(feature @ self.last_layer_style, dim=1, p=2)
        content = nn.functional.normalize(feature @ self.last_layer_content, dim=1, p=2)
        return feature, content, style
