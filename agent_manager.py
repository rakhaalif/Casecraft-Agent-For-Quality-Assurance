from typing import List, Optional
import logging


class AgentManager:
    """Dispatches generation requests to the proper agent (functional/visual).

    This is a thin orchestrator so we can keep the existing bot logic intact
    while separating responsibilities into dedicated agents.
    """

    def __init__(self, functional_agent, visual_agent):
        self.functional_agent = functional_agent
        self.visual_agent = visual_agent
        self.logger = logging.getLogger(__name__)
        # Expose the last routing decision for quick debugging/inspection
        self.last_route: Optional[str] = None

    async def generate(self, test_type: str, text: Optional[str] = None, images: Optional[List] = None) -> str:
        """Route generation to Functional or Visual agent based on test_type and presence of images.

        Rules:
        - test_type == "visual" → VisualAgent (multimodal if images are provided, else text)
        - otherwise → FunctionalAgent (multimodal if images are provided, else text)
        """
        t = (test_type or "functional").lower()
        imgs = images or []
        has_imgs = len(imgs) > 0
        self.logger.info(
            "AgentManager.generate: test_type=%s, has_images=%s, text_present=%s",
            t,
            has_imgs,
            bool(text and text.strip()),
        )

        if t == "visual":
            if has_imgs:
                self.last_route = "visual:multimodal"
                self.logger.info("Routing to VisualAgent.generate_multimodal")
                return await self.visual_agent.generate_multimodal(imgs, text or "")
            self.last_route = "visual:text"
            self.logger.info("Routing to VisualAgent.generate_from_text")
            return await self.visual_agent.generate_from_text(text or "")

        # default: functional
        if has_imgs:
            self.last_route = "functional:multimodal"
            self.logger.info("Routing to FunctionalAgent.generate_multimodal")
            return await self.functional_agent.generate_multimodal(imgs, text or "")
        self.last_route = "functional:text"
        self.logger.info("Routing to FunctionalAgent.generate_from_text")
        return await self.functional_agent.generate_from_text(text or "")

    def get_last_route(self) -> Optional[str]:
        return self.last_route

    def get_format_template(self) -> str:
        """Return a combined format guide built from agents."""
        parts = ["📋 FORMAT UNTUK GENERATE TEST CASES"]
        try:
            if self.functional_agent:
                parts.append(self.functional_agent.get_format_template())
        except Exception:
            pass
        try:
            if self.visual_agent:
                parts.append(self.visual_agent.get_format_template())
        except Exception:
            pass
        return "\n".join(p for p in parts if p)
