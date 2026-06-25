"""서명 합성(ImageProcessor) — Program-estimation v3 models/image_processor.py 이식.

인별 실제 서명 샘플(src/resources/signature/*.png)을 랜덤 변형(블러·펜압 노이즈·
mesh warp·회전·오프셋·스케일·잉크알파)해 배합일지 이미지에 합성. PIL+numpy만 의존.
"""
from PIL import Image, ImageFilter, ImageEnhance, ImageChops
import numpy as np
import random
import os
import glob
from typing import Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class ImageProcessor:
    def __init__(self, resources_path=".", config=None):
        self.resources_path = resources_path
        self.config = config or self._get_default_config()
        self.last_used_signatures = {}

    def _get_default_config(self):
        """Provides a fallback configuration if none is provided."""
        return {
            "upsample_factor": 4,
            "target_width": 70,
            "target_height": 28,
            "dpi": 300,
            "gaussian_blur_sigma": 0.7,
            "unsharp_mask": {"radius": 1.0, "percent": 120, "threshold": 2},
            "pressure_noise_strength": 0.08,
            "mesh_warp": {"grid_size": 3, "jitter_amount": 2},
            "ink_alpha_factor": 1.3,
            "signature_brightness_factor": 1.15, # Brightness enhancement (1.0 = original)
            "final_contrast_factor": 1.5, # Modified from 1.4 to 1.5
            "randomization": {
                "rotation_angle": 6,
                "offset_x": 3,
                "offset_y": 5,
                "scale_min": 0.85,
                "scale_max": 0.90
            }
        }

    def _get_random_signature_path(self, base_name, session_id):
        """
        Gets a random signature path, avoiding immediate reuse within the same session.
        """
        search_pattern = os.path.join(self.resources_path, f"{base_name}_*.png")
        file_list = glob.glob(search_pattern)
        if not file_list:
            logger.warning(f"Signature files not found for base '{base_name}'")
            return None

        last_used = self.last_used_signatures.get(session_id, {}).get(base_name)
        if last_used and len(file_list) > 1:
            available_files = [f for f in file_list if f != last_used]
            if available_files:
                file_list = available_files

        chosen_file = random.choice(file_list)
        
        if session_id not in self.last_used_signatures:
            self.last_used_signatures[session_id] = {}
        self.last_used_signatures[session_id][base_name] = chosen_file
        
        return chosen_file

    def _to_linear(self, image):
        """Converts an sRGB image to linear color space, preserving the alpha channel."""
        if image.mode != 'RGBA':
            return image.point(lambda p: ((p / 255.0) ** 2.2) * 255.0)
        
        r, g, b, a = image.split()
        rgb = Image.merge('RGB', (r, g, b))
        linear_rgb = rgb.point(lambda p: ((p / 255.0) ** 2.2) * 255.0)
        r, g, b = linear_rgb.split()
        return Image.merge('RGBA', (r, g, b, a))

    def _to_srgb(self, image):
        """Converts a linear image back to sRGB color space, preserving the alpha channel."""
        if image.mode != 'RGBA':
            return image.point(lambda p: ((p / 255.0) ** (1.0 / 2.2)) * 255.0)
            
        r, g, b, a = image.split()
        rgb = Image.merge('RGB', (r, g, b))
        srgb_rgb = rgb.point(lambda p: ((p / 255.0) ** (1.0 / 2.2)) * 255.0)
        r, g, b = srgb_rgb.split()
        return Image.merge('RGBA', (r, g, b, a))

    def _unsharp_mask(self, image, radius, percent, threshold):
        """Applies an unsharp mask filter."""
        return image.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=threshold))

    def _pressure_noise(self, alpha_channel, strength):
        """Adds Perlin-like noise to the alpha channel to simulate pen pressure variations."""
        width, height = alpha_channel.size
        
        # Generate noise as float32 and normalize to 0-1 range
        noise = np.random.normal(0, 1, (height, width)).astype(np.float32)
        noise01 = (noise - noise.min()) / (noise.max() - noise.min() + 1e-6)
        
        # Soften the noise
        noise_img = Image.fromarray((noise01 * 255).astype(np.uint8), 'L')
        noise_img = noise_img.filter(ImageFilter.GaussianBlur(radius=1.5))
        soft_noise = np.array(noise_img) / 255.0
        
        alpha_np = np.array(alpha_channel) / 255.0
        
        # Create a mask to protect thin strokes
        ink_mask = (alpha_np > 0.2).astype(np.float32)
        
        # Apply centered noise multiplicatively, using the mask
        centered_noise = soft_noise - 0.5
        noisy_alpha = alpha_np * (1.0 + (centered_noise * strength * ink_mask))
        noisy_alpha = np.clip(noisy_alpha, 0, 1)
        
        return Image.fromarray((noisy_alpha * 255).astype(np.uint8), 'L')

    def _mesh_warp(self, image, grid_size, jitter):
        """Applies a subtle mesh warp distortion to the image for a hand-drawn feel."""
        width, height = image.size
        
        x = np.linspace(0, width, grid_size)
        y = np.linspace(0, height, grid_size)
        xv, yv = np.meshgrid(x, y)
        
        jitter_x = xv + np.random.uniform(-jitter, jitter, (grid_size, grid_size))
        jitter_y = yv + np.random.uniform(-jitter, jitter, (grid_size, grid_size))
        
        jitter_x = np.clip(jitter_x, 0, width)
        jitter_y = np.clip(jitter_y, 0, height)

        source_mesh = []
        for i in range(grid_size - 1):
            for j in range(grid_size - 1):
                src_rect = (int(xv[i, j]), int(yv[i, j]), int(xv[i+1, j+1]), int(yv[i+1, j+1]))
                quad = (
                    jitter_x[i, j], yv[i, j],
                    jitter_x[i, j+1], yv[i, j+1],
                    jitter_x[i+1, j+1], yv[i+1, j+1],
                    jitter_x[i+1, j], yv[i+1, j]
                )
                source_mesh.append((src_rect, quad))

        return image.transform(image.size, Image.MESH, source_mesh, Image.Resampling.BICUBIC)

    def _multiply_blend(self, base, top, position):
        """Blends the top image onto the base image using multiply mode at a given position."""
        x, y = position
        
        # Clamp coordinates to ensure the signature is not lost at the edges
        x = max(0, min(x, base.width - top.width))
        y = max(0, min(y, base.height - top.height))
        
        box = (x, y, x + top.width, y + top.height)
        roi = base.crop(box)
        
        r_base, g_base, b_base, a_base = roi.split()
        r_top, g_top, b_top, a_top = top.split()
        
        r = ImageChops.multiply(r_base, r_top)
        g = ImageChops.multiply(g_base, g_top)
        b = ImageChops.multiply(b_base, b_top)
        
        blended_roi = Image.merge("RGB", (r, g, b))
        
        base.paste(blended_roi, box, mask=a_top)
        return base

    def _prepare_signature_alpha(self, proc_image: Image.Image, base_name: str,
                                debug_path: Optional[str]) -> Image.Image:
        """Blur, closing, clamping, and ink-effect processing on the alpha channel."""
        r, g, b, alpha = proc_image.split()
        alpha = alpha.filter(ImageFilter.GaussianBlur(self.config.get('gaussian_blur_sigma', 0.7)))
        if debug_path:
            alpha.save(os.path.join(debug_path, f"{base_name}_alpha_1_gaussian_blur.png"))

        # Closing operation to fill small holes
        alpha = alpha.filter(ImageFilter.MaxFilter(3)).filter(ImageFilter.MinFilter(3))
        if debug_path:
            alpha.save(os.path.join(debug_path, f"{base_name}_alpha_2_closing.png"))

        # Set a minimum alpha value to prevent excessive transparency
        min_alpha = int(255 * 0.35)
        alpha = alpha.point(lambda p: max(p, min_alpha) if p > 0 else 0)
        if debug_path:
            alpha.save(os.path.join(debug_path, f"{base_name}_alpha_3_clamping.png"))

        # Simulate ink properties
        alpha = self._pressure_noise(alpha, self.config.get('pressure_noise_strength', 0.0))
        if debug_path:
            alpha.save(os.path.join(debug_path, f"{base_name}_alpha_4_pressure_noise.png"))
        alpha = alpha.point(lambda p: min(255, int(p * self.config.get('ink_alpha_factor', 1.5))))
        if debug_path:
            alpha.save(os.path.join(debug_path, f"{base_name}_alpha_5_ink_alpha_factor.png"))

        proc_image.putalpha(alpha)
        return proc_image

    def _apply_enhancements(self, proc_image: Image.Image) -> Image.Image:
        """Apply unsharp mask, sRGB conversion, and brightness correction."""
        proc_image = self._unsharp_mask(
            proc_image, **self.config.get('unsharp_mask', {'radius': 1.0, 'percent': 120, 'threshold': 2})
        )
        proc_image = self._to_srgb(proc_image)
        brightness_factor = self.config.get('signature_brightness_factor', 1.0)
        if brightness_factor != 1.0:
            enhancer = ImageEnhance.Brightness(proc_image)
            proc_image = enhancer.enhance(brightness_factor)
        return proc_image

    def _apply_transform(self, proc_image: Image.Image, base_x: int, base_y: int,
                         base_name: str, debug_path: Optional[str]) -> Tuple[Image.Image, int, int]:
        """Scale, rotate, and compute paste position for a signature."""
        final_w = int(self.config.get('target_width', 70) * 0.9)
        final_h = int(self.config.get('target_height', 28) * 0.9)
        final_signature = proc_image.resize((final_w, final_h), Image.Resampling.LANCZOS)

        rand_cfg = self.config.get('randomization', {
            'rotation_angle': 6, 'offset_x': 3, 'offset_y': 5,
            'scale_min': 0.85, 'scale_max': 0.90
        })
        scale = random.uniform(rand_cfg['scale_min'], rand_cfg['scale_max'])
        angle = random.uniform(-rand_cfg['rotation_angle'], rand_cfg['rotation_angle'])
        offset_x_rand = random.randint(-rand_cfg['offset_x'], rand_cfg['offset_x'])
        offset_y_rand = random.randint(-rand_cfg['offset_y'], rand_cfg['offset_y'])

        logger.debug(f"Rand Params: scale={scale:.2f}, angle={angle:.2f}, offset=({offset_x_rand}, {offset_y_rand})")

        scaled_w, scaled_h = int(final_w * scale), int(final_h * scale)
        final_signature = final_signature.resize((scaled_w, scaled_h), Image.Resampling.LANCZOS)
        rotated_sig = final_signature.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC)

        if rotated_sig.mode == 'RGBA' and debug_path:
            rotated_alpha = rotated_sig.getchannel('A')
            reinforced_alpha_visual = rotated_alpha.filter(ImageFilter.MaxFilter(1)).filter(ImageFilter.MinFilter(1))
            reinforced_alpha_visual.save(os.path.join(debug_path, f"{base_name}_alpha_6_reinforced_after_rotation.png"))

        paste_x = base_x + offset_x_rand - (rotated_sig.width - scaled_w) // 2
        paste_y = base_y + offset_y_rand - (rotated_sig.height - scaled_h) // 2
        return rotated_sig, paste_x, paste_y

    def create_signed_image(self, base_image_path, output_path, selected_worker, debug_path=None,
                            overrides=None):
        """
        Creates a high-realism signed image by applying a sophisticated processing pipeline.

        overrides: {base_name: image_path} 로 특정 역할의 서명 원본을 직접 지정(예: 담당칸에
        작업자의 캔버스 서명을 넣어 합성과 동일한 잉크 효과로 처리). 지정 없으면 샘플 랜덤.
        """
        overrides = overrides or {}
        try:
            if debug_path:
                os.makedirs(debug_path, exist_ok=True)
            base_image = Image.open(base_image_path).convert("RGBA")
            session_id = id(self)

            charge_base_name = f"{selected_worker}_charge"
            include = self.config.get('include', {
                'charge': True, 'review': True, 'approve': True,
            })
            positions = self.config.get('positions', {
                'charge': [160, 57], 'review': [222, 54], 'approve': [288, 53]
            })

            signatures_info = []
            if include.get('charge', True):
                pos = positions.get('charge', [160, 57])
                signatures_info.append((charge_base_name, pos[0], pos[1]))
            if include.get('review', True):
                pos = positions.get('review', [222, 54])
                signatures_info.append(("review", pos[0], pos[1]))
            if include.get('approve', True):
                pos = positions.get('approve', [288, 53])
                signatures_info.append(("approve", pos[0], pos[1]))

            for base_name, base_x, base_y in signatures_info:
                sig_path = overrides.get(base_name) or self._get_random_signature_path(base_name, session_id)
                if not sig_path:
                    logger.warning(f"Signature file not found for base '{base_name}'")
                    continue

                logger.debug(f"Processing signature '{base_name}' from '{os.path.basename(sig_path)}'")
                signature_image = Image.open(sig_path).convert("RGBA")
                up_factor = self.config.get('upsample_factor', 4)
                up_w, up_h = signature_image.width * up_factor, signature_image.height * up_factor
                proc_image = signature_image.resize((up_w, up_h), Image.Resampling.LANCZOS)
                proc_image = self._to_linear(proc_image)

                proc_image = self._prepare_signature_alpha(proc_image, base_name, debug_path)
                proc_image = self._apply_enhancements(proc_image)
                rotated_sig, paste_x, paste_y = self._apply_transform(
                    proc_image, base_x, base_y, base_name, debug_path
                )
                base_image = self._multiply_blend(base_image, rotated_sig, (paste_x, paste_y))

            # 최종 이미지 전체에 대비 효과 적용
            final_contrast = self.config.get('final_contrast_factor', 1.5)
            if final_contrast != 1.0:
                enhancer = ImageEnhance.Contrast(base_image)
                base_image = enhancer.enhance(final_contrast)
                logger.debug(f"Final image contrast applied: {final_contrast}")

            dpi_value = self.config.get('dpi', 300)
            base_image.save(output_path, dpi=(dpi_value, dpi_value))
            return True, f"Signed image successfully generated: {output_path}"

        except Exception as e:
            logger.error(f"Image composition error: {e}", exc_info=True)
            return False, f"Image composition error: {e}"