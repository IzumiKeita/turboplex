"""ANSI Color utilities for TurboPlex terminal output.

Provides colorized output support for Windows PowerShell/CMD using colorama.
"""

from __future__ import annotations

# Try to import colorama for Windows compatibility
try:
    import colorama
    from colorama import Fore, Style
    _COLORAMA_AVAILABLE = True
except ImportError:
    _COLORAMA_AVAILABLE = False
    # Fallback ANSI codes if colorama not available
    class _Fore:
        GREEN = '\033[92m'
        RED = '\033[91m'
        YELLOW = '\033[93m'
        RESET = '\033[0m'
    
    class _Style:
        BRIGHT = '\033[1m'
        RESET_ALL = '\033[0m'
    
    Fore = _Fore()
    Style = _Style()


def init_colors() -> None:
    """Initialize colorama for Windows compatibility.
    
    Call this at program entry point to enable ANSI colors in Windows terminals.
    """
    if _COLORAMA_AVAILABLE:
        colorama.init(autoreset=True)


class TestStatusColors:
    """Color scheme for test status output.
    
    PASS: Verde
    FAIL: Rojo Negrita  
    ERROR: Amarillo/Naranja
    SKIPPED: Amarillo
    """
    
    @staticmethod
    def pass_text(text: str) -> str:
        """Return green colored text for PASS status."""
        return f"{Fore.GREEN}{text}{Style.RESET_ALL}"
    
    @staticmethod
    def fail_text(text: str) -> str:
        """Return red bold colored text for FAIL status."""
        return f"{Fore.RED}{Style.BRIGHT}{text}{Style.RESET_ALL}"
    
    @staticmethod
    def error_text(text: str) -> str:
        """Return yellow/orange colored text for ERROR status."""
        return f"{Fore.YELLOW}{text}{Style.RESET_ALL}"
    
    @staticmethod
    def skipped_text(text: str) -> str:
        """Return yellow colored text for SKIPPED status."""
        return f"{Fore.YELLOW}{text}{Style.RESET_ALL}"
    
    @staticmethod
    def status_letter(passed: bool, error: bool = False, skipped: bool = False) -> str:
        """Get single letter status with appropriate color.
        
        Args:
            passed: Whether test passed
            error: Whether test had an error
            skipped: Whether test was skipped
            
        Returns:
            Single character status colored appropriately
        """
        if skipped:
            return TestStatusColors.skipped_text("S")
        elif error:
            return TestStatusColors.error_text("E")
        elif passed:
            return TestStatusColors.pass_text("P")
        else:
            return TestStatusColors.fail_text("F")
    
    @staticmethod
    def status_word(passed: bool, error: bool = False, skipped: bool = False) -> str:
        """Get full word status with appropriate color.
        
        Args:
            passed: Whether test passed
            error: Whether test had an error  
            skipped: Whether test was skipped
            
        Returns:
            Full status word colored appropriately
        """
        if skipped:
            return TestStatusColors.skipped_text("SKIPPED")
        elif error:
            return TestStatusColors.error_text("ERROR")
        elif passed:
            return TestStatusColors.pass_text("PASS")
        else:
            return TestStatusColors.fail_text("FAIL")


# Convenience functions for direct use
def green(text: str) -> str:
    """Return green colored text."""
    return f"{Fore.GREEN}{text}{Style.RESET_ALL}"


def red(text: str) -> str:
    """Return red colored text."""
    return f"{Fore.RED}{text}{Style.RESET_ALL}"


def red_bold(text: str) -> str:
    """Return red bold colored text."""
    return f"{Fore.RED}{Style.BRIGHT}{text}{Style.RESET_ALL}"


def yellow(text: str) -> str:
    """Return yellow colored text."""
    return f"{Fore.YELLOW}{text}{Style.RESET_ALL}"


def reset() -> str:
    """Return reset code."""
    return Style.RESET_ALL
